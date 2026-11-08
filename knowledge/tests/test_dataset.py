"""Contract tests for loading the reviewed MuseBridge evidence release."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from knowledge import Dataset, DatasetValidationError, load_dataset


SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "aic-seed-v0.1.json"


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "seed.json"

    def write_seed(self, document=None):
        if document is None:
            document = self.seed
        self.path.write_text(
            json.dumps(document, ensure_ascii=True), encoding="utf-8"
        )
        return self.path

    def assert_invalid(self, document):
        self.write_seed(document)
        with self.assertRaises(DatasetValidationError):
            load_dataset(self.path)

    def test_real_release_loads_with_expected_public_containers(self):
        dataset = load_dataset(SEED_PATH)
        self.assertIsInstance(dataset, Dataset)
        self.assertEqual(dataset.dataset_version, "aic-seed-v0.1")
        self.assertEqual(set(dataset.artworks), {"aic-28560", "aic-11723"})
        self.assertEqual(set(dataset.sources), {"aic-api-28560", "aic-api-11723"})
        self.assertIsInstance(dataset.chunks, tuple)
        self.assertEqual(len(dataset.chunks), 4)
        for raw in self.seed["artworks"]:
            self.assertEqual(dict(dataset.artworks[raw["artwork_id"]]), raw)
        for raw in self.seed["sources"]:
            self.assertEqual(dict(dataset.sources[raw["source_id"]]), raw)
        chunks = {chunk["chunk_id"]: chunk for chunk in dataset.chunks}
        for raw in self.seed["chunks"]:
            loaded = chunks[raw["chunk_id"]]
            self.assertEqual(loaded["excerpt"], raw["excerpt"])
            self.assertEqual(loaded["topics"], tuple(raw["topics"]))
            self.assertIsInstance(loaded["topics"], tuple)

    def test_string_path_is_supported(self):
        dataset = load_dataset(str(SEED_PATH))
        self.assertEqual(dataset.dataset_version, self.seed["dataset_version"])

    def test_loaded_dataset_is_deeply_immutable(self):
        dataset = load_dataset(SEED_PATH)
        with self.assertRaises(AttributeError):
            dataset.dataset_version = "changed"
        with self.assertRaises(TypeError):
            dataset.artworks["new"] = {}
        with self.assertRaises(TypeError):
            dataset.sources["new"] = {}
        with self.assertRaises(TypeError):
            dataset.artworks["aic-28560"]["title"] = "changed"
        with self.assertRaises(TypeError):
            dataset.sources["aic-api-28560"]["source_url"] = "changed"
        with self.assertRaises(TypeError):
            dataset.chunks[0]["excerpt"] = "changed"
        with self.assertRaises(TypeError):
            dataset.chunks[0]["topics"][0] = "changed"
        with self.assertRaises(TypeError):
            dataset.chunks[0] = {}

    def test_records_are_validated_without_hardcoding_source_text_or_file_hash(self):
        document = copy.deepcopy(self.seed)
        document["artworks"][0]["title"] = "Reviewed catalog title"
        document["sources"][0]["source_title"] = (
            "Reviewed catalog title — Art Institute of Chicago API record"
        )
        document["chunks"][0]["excerpt"] = "A separately reviewed source excerpt."
        dataset = load_dataset(self.write_seed(document))
        self.assertEqual(dataset.artworks["aic-28560"]["title"], "Reviewed catalog title")
        self.assertEqual(dataset.chunks[0]["excerpt"], document["chunks"][0]["excerpt"])

    def test_configured_version_must_match_root_and_all_artworks(self):
        with self.assertRaises(DatasetValidationError):
            load_dataset(SEED_PATH, expected_version="aic-seed-v0.2")
        for target in ("root", "artwork"):
            with self.subTest(target=target):
                document = copy.deepcopy(self.seed)
                if target == "root":
                    document["dataset_version"] = "aic-seed-v0.2"
                else:
                    document["artworks"][0]["dataset_version"] = "aic-seed-v0.2"
                self.assert_invalid(document)

    def test_explicit_matching_release_pin_is_not_globally_hardcoded(self):
        document = copy.deepcopy(self.seed)
        document["dataset_version"] = "aic-seed-v0.2"
        for artwork in document["artworks"]:
            artwork["dataset_version"] = "aic-seed-v0.2"
        dataset = load_dataset(
            self.write_seed(document), expected_version="aic-seed-v0.2"
        )
        self.assertEqual(dataset.dataset_version, "aic-seed-v0.2")

    def test_expected_version_requires_nonempty_string(self):
        for value in ("", None, True, 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(DatasetValidationError):
                    load_dataset(SEED_PATH, expected_version=value)

    def test_missing_file_raises_domain_error(self):
        with self.assertRaises(DatasetValidationError):
            load_dataset(Path(self.temp_dir.name) / "missing.json")

    def test_directory_path_raises_domain_error(self):
        with self.assertRaises(DatasetValidationError):
            load_dataset(Path(self.temp_dir.name))

    def test_invalid_json_and_non_object_roots_are_rejected(self):
        bodies = ("", "{", "{} {}", "[]", "null", "42", '"seed"', "true")
        for body in bodies:
            with self.subTest(body=body):
                self.path.write_text(body, encoding="utf-8")
                with self.assertRaises(DatasetValidationError):
                    load_dataset(self.path)

    def test_invalid_utf8_and_bom_are_rejected(self):
        valid = json.dumps(self.seed).encode("utf-8")
        for body in (b"\xff", b"\xef\xbb\xbf" + valid):
            with self.subTest(prefix=body[:3]):
                self.path.write_bytes(body)
                with self.assertRaises(DatasetValidationError):
                    load_dataset(self.path)

    def test_duplicate_json_keys_are_rejected_at_any_depth(self):
        valid = json.dumps(self.seed)
        bodies = (
            valid.replace(
                '"dataset_version":',
                '"dataset_version": "ignored", "dataset_version":',
                1,
            ),
            valid.replace(
                '"excerpt":', '"excerpt": "ignored", "excerpt":', 1
            ),
        )
        for body in bodies:
            with self.subTest(body=body[:100]):
                self.path.write_text(body, encoding="utf-8")
                with self.assertRaises(DatasetValidationError):
                    load_dataset(self.path)

    def test_oversized_json_integer_raises_domain_error(self):
        # On newer Python versions json's integer guard raises ValueError.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large-integer.json"
            path.write_text("9" * 4400, encoding="utf-8")
            with self.assertRaises(DatasetValidationError):
                load_dataset(path)

    def test_nonfinite_json_numbers_are_rejected(self):
        valid = json.dumps(self.seed)
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                body = valid.replace('"museum_record_id": 28560', '"museum_record_id": ' + literal, 1)
                self.path.write_text(body, encoding="utf-8")
                with self.assertRaises(DatasetValidationError):
                    load_dataset(self.path)

    def test_root_requires_exact_fields_and_array_collections(self):
        for action in ("missing", "extra"):
            with self.subTest(action=action):
                document = copy.deepcopy(self.seed)
                if action == "missing":
                    del document["sources"]
                else:
                    document["answers"] = []
                self.assert_invalid(document)
        for collection in ("artworks", "sources", "chunks"):
            for value in ({}, None, "records"):
                with self.subTest(collection=collection, value=value):
                    document = copy.deepcopy(self.seed)
                    document[collection] = value
                    self.assert_invalid(document)

    def test_release_cardinalities_are_enforced(self):
        for collection in ("artworks", "sources", "chunks"):
            for operation in ("remove", "append"):
                with self.subTest(collection=collection, operation=operation):
                    document = copy.deepcopy(self.seed)
                    if operation == "remove":
                        document[collection].pop()
                    else:
                        document[collection].append(copy.deepcopy(document[collection][0]))
                    self.assert_invalid(document)

    def test_records_require_exact_fields_and_object_types(self):
        for collection in ("artworks", "sources", "chunks"):
            for action in ("missing", "extra", "non_object"):
                with self.subTest(collection=collection, action=action):
                    document = copy.deepcopy(self.seed)
                    record = document[collection][0]
                    if action == "missing":
                        del record[next(iter(record))]
                    elif action == "extra":
                        record["unreviewed"] = "extra"
                    else:
                        document[collection][0] = []
                    self.assert_invalid(document)

    def test_required_strings_cannot_be_empty_or_wrong_types(self):
        fields = (("artworks", "artist"), ("sources", "institution"), ("chunks", "excerpt"))
        for collection, field in fields:
            for value in ("", None, True, 4, [], {}):
                with self.subTest(collection=collection, field=field, value=value):
                    document = copy.deepcopy(self.seed)
                    document[collection][0][field] = value
                    self.assert_invalid(document)

    def test_museum_record_id_is_an_integer_not_a_boolean_or_coerced_number(self):
        for value in (True, False, 0, -1, 28560.0, "28560", None):
            with self.subTest(value=value):
                document = copy.deepcopy(self.seed)
                document["artworks"][0]["museum_record_id"] = value
                self.assert_invalid(document)

    def test_images_are_not_part_of_this_release(self):
        document = copy.deepcopy(self.seed)
        document["artworks"][0]["image_url"] = "https://example.com/image.jpg"
        self.assert_invalid(document)

    def test_duplicate_record_ids_and_slugs_are_rejected(self):
        for collection, key in (
            ("artworks", "artwork_id"),
            ("artworks", "slug"),
            ("sources", "source_id"),
            ("chunks", "chunk_id"),
        ):
            with self.subTest(collection=collection, key=key):
                document = copy.deepcopy(self.seed)
                document[collection][1][key] = document[collection][0][key]
                self.assert_invalid(document)

    def test_unknown_release_ids_and_references_are_rejected(self):
        for collection, key in (
            ("artworks", "artwork_id"),
            ("artworks", "slug"),
            ("sources", "source_id"),
            ("sources", "artwork_id"),
            ("chunks", "chunk_id"),
            ("chunks", "artwork_id"),
            ("chunks", "source_id"),
        ):
            with self.subTest(collection=collection, key=key):
                document = copy.deepcopy(self.seed)
                document[collection][0][key] = "not-in-this-release"
                self.assert_invalid(document)

    def test_cross_artwork_source_links_are_rejected(self):
        for collection, key, value in (
            ("sources", "artwork_id", "aic-11723"),
            ("chunks", "artwork_id", "aic-11723"),
            ("chunks", "source_id", "aic-api-11723"),
        ):
            with self.subTest(collection=collection, key=key):
                document = copy.deepcopy(self.seed)
                document[collection][0][key] = value
                self.assert_invalid(document)

    def test_official_source_metadata_must_match_the_released_artwork(self):
        mutations = (
            ("artworks", "source_url", "http://api.artic.edu/api/v1/artworks/28560"),
            ("artworks", "source_url", "https://api.artic.edu/api/v1/artworks/11723"),
            ("artworks", "museum", "Different museum"),
            ("sources", "source_url", "https://example.com/28560"),
            ("sources", "source_title", "Unrelated catalog title"),
            ("sources", "source_language", "zh-CN"),
            ("sources", "institution", "Different museum"),
            ("sources", "terms_url", "https://example.com/terms"),
        )
        for collection, key, value in mutations:
            with self.subTest(collection=collection, key=key, value=value):
                document = copy.deepcopy(self.seed)
                document[collection][0][key] = value
                self.assert_invalid(document)

    def test_source_timestamps_require_real_rfc3339_utc_dates(self):
        for value in (
            "2026-02-30T12:00:00Z",
            "2026-11-08",
            "2026-11-08T02:53:29",
            "2026-11-08T02:53:29-08:00",
            "yesterday",
        ):
            with self.subTest(value=value):
                document = copy.deepcopy(self.seed)
                document["sources"][0]["retrieved_at"] = value
                self.assert_invalid(document)

    def test_old_utc_timestamps_and_zero_offset_are_valid(self):
        for value in ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00+00:00"):
            with self.subTest(value=value):
                document = copy.deepcopy(self.seed)
                document["sources"][0]["retrieved_at"] = value
                dataset = load_dataset(self.write_seed(document))
                self.assertEqual(dataset.sources["aic-api-28560"]["retrieved_at"], value)

    def test_chunk_locator_license_and_attribution_are_field_specific(self):
        mutations = (
            (0, "source_locator", "data.description, paragraph 1"),
            (1, "source_locator", "data.medium_display"),
            (0, "license", "CC-BY-4.0"),
            (1, "license", "CC0-1.0"),
            (0, "license_url", "https://creativecommons.org/licenses/by/4.0/"),
            (1, "license_url", "https://creativecommons.org/publicdomain/zero/1.0/"),
            (1, "attribution", "Unknown"),
            (0, "normalization", "HTML removed; whitespace normalized."),
            (1, "normalization", "none"),
            (3, "normalization", "HTML removed; whitespace normalized."),
        )
        for index, key, value in mutations:
            with self.subTest(index=index, key=key):
                document = copy.deepcopy(self.seed)
                document["chunks"][index][key] = value
                self.assert_invalid(document)

    def test_excerpt_limit_counts_unicode_code_points(self):
        document = copy.deepcopy(self.seed)
        document["chunks"][0]["excerpt"] = "\U0001f3a8" * 4000
        loaded = load_dataset(self.write_seed(document))
        self.assertEqual(len(loaded.chunks[0]["excerpt"]), 4000)
        document["chunks"][0]["excerpt"] += "\U0001f3a8"
        self.assert_invalid(document)

    def test_excerpt_rejects_control_characters_and_unpaired_surrogates(self):
        for value in ("before\x00after", "before\x01after", "before\x7fafter", "\ud800", "\udfff"):
            with self.subTest(value=ascii(value)):
                document = copy.deepcopy(self.seed)
                document["chunks"][0]["excerpt"] = value
                self.assert_invalid(document)

    def test_topics_are_unique_and_match_the_published_chunk_scope(self):
        values = ([], None, "material", ["material", "material"], ["unknown"], ["brushwork"])
        for value in values:
            with self.subTest(value=value):
                document = copy.deepcopy(self.seed)
                document["chunks"][0]["topics"] = value
                self.assert_invalid(document)
        document = copy.deepcopy(self.seed)
        document["chunks"][1]["topics"] = ["brushwork"]
        self.assert_invalid(document)


if __name__ == "__main__":
    unittest.main()
