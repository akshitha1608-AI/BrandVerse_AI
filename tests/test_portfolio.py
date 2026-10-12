import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from app import create_app
from backend.extensions import db
from backend.models import Project, ProjectAnalyticsEvent, ProjectBookmark, ProjectLike, ProjectMedia, User


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "portfolio-tests-secret",
            "JWT_SECRET_KEY": "portfolio-tests-jwt-secret",
            "SEED_DEMO": True,
            "UPLOAD_FOLDER": self.uploads.name,
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        self.creator = User.query.filter_by(email="aarohi@demo.creatorbridge.test").first()
        self.other_creator = User.query.filter_by(email="rayan@demo.creatorbridge.test").first()
        self.brand = User.query.filter_by(email="novafit@demo.creatorbridge.test").first()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.uploads.cleanup()

    def login_as(self, user):
        with self.client.session_transaction() as session:
            session["user_id"] = user.id
            session["_csrf_token"] = f"csrf-for-{user.id}"

    def project_form(self, **overrides):
        data = {
            "csrf_token": f"csrf-for-{self.creator.id}",
            "title": "Evening light study",
            "project_type": "photography",
            "visibility": "public",
            "progress": "completed",
            "action": "publish",
            "description": "A photographic study in natural light.",
            "category": "Editorial",
            "tags": "portrait, natural light",
            "project_date": "",
        }
        data.update(overrides)
        return data

    def create_published_project(self, visibility="public", status="published"):
        project = Project(
            user_id=self.creator.id,
            title="Public study",
            project_type="photography",
            visibility=visibility,
            status=status,
            description="A project description.",
        )
        db.session.add(project)
        db.session.commit()
        return project

    def test_showcase_feeds_and_creator_portfolio_render(self):
        self.create_published_project()
        self.login_as(self.creator)
        for path in (
            "/showcase",
            "/showcase?sort=popular",
            "/showcase?type=photography",
            "/showcase?feed=new-creators",
            "/showcase?feed=featured-portfolios",
            f"/creator/{self.creator.creator_profile.username}",
            "/portfolio",
            "/portfolio/analytics",
            "/projects/new?type=video",
            "/saved-projects",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_creator_can_publish_image_project_with_caption_and_storage(self):
        self.login_as(self.creator)
        data = self.project_form(
            media=(BytesIO(PNG_BYTES), "portrait.png"),
            new_media_caption=["Soft evening window light"],
        )
        response = self.client.post("/projects/new", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        project = Project.query.filter_by(title="Evening light study").one()
        media = ProjectMedia.query.filter_by(project_id=project.id).one()
        stored = Path(self.app.config["UPLOAD_FOLDER"]) / "portfolio" / media.storage_name
        self.assertTrue(stored.is_file())
        self.assertEqual(media.caption, "Soft evening window light")
        self.assertEqual(self.client.get(f"/projects/{project.id}").status_code, 200)
        media_response = self.client.get(f"/project-media/{media.id}")
        self.assertEqual(media_response.status_code, 200)
        media_response.close()

    def test_invalid_upload_does_not_publish_project(self):
        self.login_as(self.creator)
        data = self.project_form(media=(BytesIO(b"not a png"), "fake.png"))
        response = self.client.post("/projects/new", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Project.query.count(), 0)
        self.assertEqual(list((Path(self.uploads.name) / "portfolio").glob("**/*")) if (Path(self.uploads.name) / "portfolio").exists() else [], [])

    def test_public_discovery_excludes_unlisted_private_and_drafts(self):
        public = self.create_published_project()
        unlisted = self.create_published_project("unlisted")
        private = self.create_published_project("private")
        draft = self.create_published_project("public", "draft")
        listing = self.client.get("/showcase")
        self.assertIn(f"/projects/{public.id}".encode(), listing.data)
        self.assertNotIn(f"/projects/{unlisted.id}".encode(), listing.data)
        unlisted_page = self.client.get(f"/projects/{unlisted.id}")
        self.assertEqual(unlisted_page.status_code, 200)
        self.assertIn(b"noindex, nofollow", unlisted_page.data)
        for hidden in (private, draft):
            self.assertNotIn(f"/projects/{hidden.id}".encode(), listing.data)
            self.assertEqual(self.client.get(f"/projects/{hidden.id}").status_code, 404)

    def test_creator_ownership_and_role_boundaries(self):
        project = self.create_published_project()
        self.login_as(self.other_creator)
        self.assertEqual(self.client.get(f"/projects/{project.id}/edit").status_code, 404)
        self.login_as(self.brand)
        self.assertEqual(self.client.get("/portfolio").status_code, 403)
        self.assertEqual(self.client.get("/projects/new").status_code, 403)

    def test_edit_updates_media_caption_and_delete_removes_storage(self):
        self.login_as(self.creator)
        create_data = self.project_form(media=(BytesIO(PNG_BYTES), "portrait.png"))
        self.client.post("/projects/new", data=create_data, content_type="multipart/form-data")
        project = Project.query.filter_by(title="Evening light study").one()
        media = project.media[0]
        stored = Path(self.app.config["UPLOAD_FOLDER"]) / "portfolio" / media.storage_name
        edit_data = self.project_form(
            title="Evening light, revised",
            action="draft",
            cover_media_id=str(media.id),
            **{f"media_caption_{media.id}": "Updated image caption"},
        )
        response = self.client.post(f"/projects/{project.id}/edit", data=edit_data)
        self.assertEqual(response.status_code, 302)
        db.session.refresh(media)
        self.assertEqual(media.caption, "Updated image caption")
        self.assertEqual(project.status, "draft")
        delete_data = {"csrf_token": f"csrf-for-{self.creator.id}"}
        self.assertEqual(self.client.post(f"/projects/{project.id}/delete", data=delete_data).status_code, 302)
        self.assertIsNone(db.session.get(Project, project.id))
        self.assertFalse(stored.exists())

    def test_likes_bookmarks_and_unique_view_analytics(self):
        project = self.create_published_project()
        self.client.get(f"/projects/{project.id}")
        self.client.get(f"/projects/{project.id}")
        self.assertEqual(ProjectAnalyticsEvent.query.filter_by(project_id=project.id, event_type="view").count(), 1)
        self.login_as(self.other_creator)
        csrf = f"csrf-for-{self.other_creator.id}"
        self.client.post(f"/projects/{project.id}/like", data={"csrf_token": csrf})
        self.client.post(f"/projects/{project.id}/save", data={"csrf_token": csrf})
        self.assertEqual(ProjectLike.query.filter_by(project_id=project.id, user_id=self.other_creator.id).count(), 1)
        self.assertEqual(ProjectBookmark.query.filter_by(project_id=project.id, user_id=self.other_creator.id).count(), 1)
        saved_page = self.client.get("/saved-projects")
        self.assertEqual(saved_page.status_code, 200)
        self.assertIn(f"/projects/{project.id}".encode(), saved_page.data)

    def test_video_analytics_track_public_playback_but_not_owner_activity(self):
        project = self.create_published_project()
        video = ProjectMedia(
            project_id=project.id,
            storage_name=f"{self.creator.id}/video.mp4",
            original_name="video.mp4",
            media_type="video",
            mime_type="video/mp4",
            file_size=100,
        )
        db.session.add(video)
        db.session.commit()
        endpoint = f"/projects/{project.id}/analytics"
        owner_token = f"csrf-for-{self.creator.id}"
        self.login_as(self.creator)
        owner_response = self.client.post(endpoint, data={"csrf_token": owner_token, "event_type": "video_start"})
        self.assertEqual(owner_response.status_code, 204)
        self.assertEqual(ProjectAnalyticsEvent.query.filter_by(project_id=project.id, event_type="video_start").count(), 0)
        self.login_as(self.other_creator)
        visitor_token = f"csrf-for-{self.other_creator.id}"
        self.client.post(endpoint, data={"csrf_token": visitor_token, "event_type": "video_start"})
        self.client.post(endpoint, data={"csrf_token": visitor_token, "event_type": "video_start"})
        self.assertEqual(ProjectAnalyticsEvent.query.filter_by(project_id=project.id, event_type="video_start").count(), 1)


if __name__ == "__main__":
    unittest.main()
