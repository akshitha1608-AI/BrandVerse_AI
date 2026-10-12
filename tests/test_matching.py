import unittest

from backend.matching.recommendation import budget_score, rank_creators, score_match


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.campaign = {
            "category": "Fashion",
            "minimum_budget": 20000,
            "maximum_budget": 50000,
            "target_audience": "College students",
            "content_format": "Reels",
            "objective": "Brand awareness",
        }

    def test_score_is_bounded_and_explained(self):
        creator = {
            "id": 1, "category": "Fashion", "categories": ["Fashion"], "minimum_budget": 25000,
            "maximum_budget": 40000, "audience": "College students", "formats": ["Reels"],
            "skills": ["Brand awareness"], "available": True,
        }
        result = score_match(creator, self.campaign)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)
        self.assertTrue(result["explanation"])
        self.assertEqual(set(result["components"]), {"category", "budget", "audience", "format", "objective", "availability"})

    def test_better_fit_scores_higher(self):
        strong = {
            "id": 1, "name": "Strong", "category": "Fashion", "minimum_budget": 25000, "maximum_budget": 40000,
            "audience": "College students", "formats": ["Reels"], "skills": ["Brand awareness"], "available": True,
        }
        weak = {
            "id": 2, "name": "Weak", "category": "Gaming", "minimum_budget": 90000, "maximum_budget": 120000,
            "audience": "Gaming fans", "formats": ["Live"], "skills": [], "available": True,
        }
        result = rank_creators([weak, strong], self.campaign)
        self.assertEqual([item["id"] for item in result], [1])
        self.assertGreater(result[0]["match"]["score"], score_match(weak, self.campaign)["score"])

    def test_over_budget_creator_excluded_unless_negotiation_allowed(self):
        creator = {"id": 1, "minimum_budget": 60000, "maximum_budget": 90000}
        self.assertEqual(budget_score(creator, self.campaign), 0)
        self.assertGreater(budget_score(creator, {**self.campaign, "allow_negotiation": True}), 0)

    def test_missing_data_is_not_a_perfect_match(self):
        result = score_match({}, self.campaign)
        self.assertLess(result["score"], 100)
        self.assertEqual(result["components"]["category"], 0)
        self.assertIn("budget", result["gaps"])

    def test_unavailable_creator_receives_availability_penalty(self):
        available = {"id": 1, "minimum_budget": 20000, "maximum_budget": 50000, "available": True}
        unavailable = {**available, "available": False}
        self.assertGreater(score_match(available, self.campaign)["score"], score_match(unavailable, self.campaign)["score"])

    def test_related_categories_receive_partial_credit(self):
        result = score_match(
            {"category": "Beauty", "minimum_budget": 20000, "maximum_budget": 50000, "available": True},
            self.campaign,
        )
        self.assertEqual(result["components"]["category"], 60)

    def test_missing_availability_is_not_perfect(self):
        creator = {"minimum_budget": 20000, "maximum_budget": 50000}
        self.assertEqual(score_match(creator, self.campaign)["components"]["availability"], 0)


if __name__ == "__main__":
    unittest.main()
