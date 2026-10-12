import unittest
import re

from app import create_app
from backend.extensions import db
from backend.models import Campaign, User
from backend.utils.portfolio_parser import extract_portfolio_text, suggest_profile_fields
from io import BytesIO
from werkzeug.datastructures import FileStorage


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "test-secret-key-long-enough-for-tests",
            "JWT_SECRET_KEY": "test-jwt-secret-key-long-enough-for-tests",
            "SEED_DEMO": True,
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def token_for(self, email, password="DemoPass123!"):
        response = self.client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200)
        return response.json["access_token"]

    def test_demo_records_are_created(self):
        self.assertEqual(User.query.filter_by(role="creator").count(), 12)
        self.assertEqual(User.query.filter_by(role="brand").count(), 4)
        self.assertEqual(Campaign.query.count(), 10)

    def test_public_lists_are_paginated(self):
        creators = self.client.get("/api/creators?page=2&per_page=5")
        self.assertEqual(creators.status_code, 200)
        self.assertEqual(creators.json["total"], 12)
        self.assertEqual(len(creators.json["items"]), 5)
        campaigns = self.client.get("/api/campaigns?page=1&per_page=3")
        self.assertEqual(campaigns.json["total"], 10)
        self.assertEqual(len(campaigns.json["items"]), 3)

    def test_password_is_hashed_and_login_works(self):
        user = User.query.filter_by(email="aarohi@demo.creatorbridge.test").first()
        self.assertNotEqual(user.password_hash, "DemoPass123!")
        self.assertTrue(user.check_password("DemoPass123!"))
        self.assertTrue(self.token_for(user.email))

    def test_registration_validates_input(self):
        response = self.client.post("/api/auth/register", json={
            "name": "New Creator", "email": "not-an-email", "password": "short", "role": "creator",
        })
        self.assertEqual(response.status_code, 400)

    def test_auth_rejects_non_object_json(self):
        response = self.client.post("/api/auth/register", json=[])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "Expected a JSON object")

    def test_brand_can_create_campaign_and_creator_can_apply_once(self):
        brand_token = self.token_for("novafit@demo.creatorbridge.test")
        response = self.client.post("/api/campaigns", headers={"Authorization": f"Bearer {brand_token}"}, json={
            "title": "Test campaign", "description": "Test brief", "category": "Fashion",
            "target_audience": "College students", "content_format": "Reels",
            "minimum_budget": 20000, "maximum_budget": 40000,
        })
        self.assertEqual(response.status_code, 201)
        campaign_id = response.json["id"]
        creator_token = self.token_for("aarohi@demo.creatorbridge.test")
        url = f"/api/campaigns/{campaign_id}/apply"
        headers = {"Authorization": f"Bearer {creator_token}"}
        self.assertEqual(self.client.post(url, headers=headers, json={}).status_code, 201)
        self.assertEqual(self.client.post(url, headers=headers, json={}).status_code, 409)

    def test_campaign_ownership_is_enforced_for_recommendations(self):
        brand_one = self.token_for("novafit@demo.creatorbridge.test")
        brand_two = self.token_for("pixelnest@demo.creatorbridge.test")
        campaign = Campaign.query.filter_by(owner_id=User.query.filter_by(email="novafit@demo.creatorbridge.test").first().id).first()
        response = self.client.get(f"/api/campaigns/{campaign.id}/recommendations", headers={"Authorization": f"Bearer {brand_two}"})
        self.assertEqual(response.status_code, 403)
        allowed = self.client.get(f"/api/campaigns/{campaign.id}/recommendations", headers={"Authorization": f"Bearer {brand_one}"})
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(all("match" in creator for creator in allowed.json))

    def test_creator_cannot_create_brand_campaign(self):
        creator_token = self.token_for("aarohi@demo.creatorbridge.test")
        response = self.client.post("/api/campaigns", headers={"Authorization": f"Bearer {creator_token}"}, json={})
        self.assertEqual(response.status_code, 403)

    def test_creator_recommendations_are_ranked_and_campaign_specific(self):
        token = self.token_for("aarohi@demo.creatorbridge.test")
        headers = {"Authorization": f"Bearer {token}"}
        fashion = Campaign.query.filter_by(title="Everyday Style Edit").first()
        gaming = Campaign.query.filter_by(title="Game On").first()
        fashion_results = self.client.get("/api/recommendations/campaigns", headers=headers).json
        self.assertGreater(len(fashion_results), 0)
        scores = [item["match"]["score"] for item in fashion_results]
        self.assertEqual(scores, sorted(scores, reverse=True))
        campaign_match = self.client.get(f"/api/campaigns/{fashion.id}", headers=headers).json
        self.assertEqual(campaign_match["category"], "Fashion")
        self.assertNotEqual(fashion.category, gaming.category)

    def test_invalid_campaign_creator_count_is_rejected(self):
        token = self.token_for("novafit@demo.creatorbridge.test")
        response = self.client.post("/api/campaigns", headers={"Authorization": f"Bearer {token}"}, json={
            "title": "Test campaign", "description": "Test brief", "category": "Fashion",
            "target_audience": "College students", "content_format": "Reels",
            "minimum_budget": 20000, "maximum_budget": 40000, "creators_required": "many",
        })
        self.assertEqual(response.status_code, 400)

    def test_private_api_requires_token(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_public_marketplace_pages_render(self):
        for path in ("/", "/creators", "/campaigns", "/register", "/login"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_role_dashboards_and_profile_pages_render(self):
        creator = User.query.filter_by(email="aarohi@demo.creatorbridge.test").first()
        brand = User.query.filter_by(email="novafit@demo.creatorbridge.test").first()
        for user in (creator, brand):
            with self.subTest(role=user.role):
                with self.client.session_transaction() as session:
                    session["user_id"] = user.id
                    session["_csrf_token"] = "test-csrf-token"
                self.assertEqual(self.client.get("/dashboard").status_code, 200)
                self.assertEqual(self.client.get("/profile").status_code, 200)
                if user.role == "brand":
                    self.assertEqual(self.client.get("/campaigns/new").status_code, 200)

    def test_campaign_detail_and_owner_match_page_render(self):
        campaign = Campaign.query.filter_by(status="published").first()
        self.assertEqual(self.client.get(f"/campaigns/{campaign.id}").status_code, 200)
        brand = campaign.owner
        with self.client.session_transaction() as session:
            session["user_id"] = brand.id
        response = self.client.get(f"/campaigns/{campaign.id}/creator-matches")
        self.assertEqual(response.status_code, 200)

    def test_registration_form_requires_csrf_and_creates_account(self):
        response = self.client.get("/register")
        csrf_token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        response = self.client.post("/register", data={
            "csrf_token": csrf_token,
            "name": "Test Creator",
            "email": "test.creator@example.com",
            "password": "SecurePass123",
            "role": "creator",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request.path, "/dashboard")
        self.assertIn(b"creator dashboard", response.data)
        user = User.query.filter_by(email="test.creator@example.com").first()
        self.assertIsNotNone(user)
        self.assertTrue(user.creator_profile)

    def test_pdf_upload_rejects_wrong_type_and_bad_header(self):
        invalid_extension = FileStorage(stream=BytesIO(b"%PDF-fake"), filename="portfolio.txt")
        with self.assertRaisesRegex(ValueError, "Only PDF"):
            extract_portfolio_text(invalid_extension)
        invalid_pdf = FileStorage(stream=BytesIO(b"not a pdf"), filename="portfolio.pdf")
        with self.assertRaisesRegex(ValueError, "not a valid PDF"):
            extract_portfolio_text(invalid_pdf)

    def test_portfolio_keywords_are_suggestions_only(self):
        suggestions = suggest_profile_fields("Fashion reels, beauty styling, and short-form video tutorials.")
        self.assertIn("fashion", suggestions["categories"])
        self.assertIn("beauty", suggestions["categories"])
        self.assertIn("short-form video", suggestions["formats"])
        self.assertIn("tutorials", suggestions["skills"])

    def test_profile_api_rejects_out_of_range_engagement_rate(self):
        token = self.token_for("aarohi@demo.creatorbridge.test")
        response = self.client.put("/api/creators/profile", headers={"Authorization": f"Bearer {token}"}, json={
            "engagement_rate": 120,
        })
        self.assertEqual(response.status_code, 400)

    def test_logout_revokes_access_token(self):
        token = self.token_for("aarohi@demo.creatorbridge.test")
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
