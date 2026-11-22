"""Spatial vocabulary can rule out an unsupported qualifier; it cannot grant support."""
import unittest

from agent.runtime.spatial_support import missing_spatial_detail


class SpatialSupportTests(unittest.TestCase):
    def test_different_subjects_and_axes_need_an_explicit_spatial_fact(self):
        for question, source in [
            ("Does the description mention wallpaper on the left side of the painting?", "Wallpaper is suggested."),
            ("Is the vessel on the right?", "The potter had the right tools."),
            ("Is the cup on the left?", "The cup left the workshop."),
            ("Is the moon in the upper corner?", "A moon appears above the figure."),
            ("Does the inventory place the bird at the bottom?", "The bird has brown feathers."),
            ("Is the handle above the bowl?", "The handle is on the bowl."),
            ("Does the writer place the vessel below the table?", "The vessel is on the lower edge."),
            ("Is the second vessel in the foreground?", "Two vessels appear in the background."),
        ]:
            with self.subTest(question=question):
                self.assertTrue(missing_spatial_detail(question, [source]))

    def test_equivalent_explicit_source_words_do_not_trigger_the_veto(self):
        for question, source in [
            ("Is the bird on the left side?", "A bird rests at the left-hand edge."),
            ("Are they in the lower half?", "They are at the bottom."),
            ("Is the line below the handle?", "The line is beneath the handle."),
            ("Does the account locate the figure in the background?", "The figure is at the back of the scene."),
        ]:
            with self.subTest(question=question):
                self.assertFalse(missing_spatial_detail(question, [source]))

    def test_incidental_location_or_idiom_is_not_a_spatial_question(self):
        for question in ("What color is the cloth on the right?", "Is the figure on the left wearing a coat?",
                         "Does the museum say the figure on the right is reading?", "Is that the right word?",
                         "What did the artist leave behind?", "Is the bird above the door blue?",
                         "Is the chair at the top or bottom?"):
            with self.subTest(question=question):
                self.assertFalse(missing_spatial_detail(question, ["The cloth is red."]))
