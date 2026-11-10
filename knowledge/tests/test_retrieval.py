import json
import tempfile
import unittest
from pathlib import Path

from knowledge import RetrievalError, load_dataset, retrieve
from knowledge.retrieval import retrieve_with_trace


ROOT = Path(__file__).resolve().parents[2]
VERSION = "aic-seed-v0.1"
SEED_PATH = ROOT / "knowledge" / "data" / f"{VERSION}.json"
EVAL_PATH = ROOT / "eval" / f"{VERSION}.tests.json"

BEDROOM = "aic-28560"
TOILETTE = "aic-11723"
BEDROOM_MEDIUM = "aic-28560-medium"
BEDROOM_DESCRIPTION = "aic-28560-description-01"
TOILETTE_MEDIUM = "aic-11723-medium"
TOILETTE_DESCRIPTION = "aic-11723-description-01"
BRUSHWORK_QUESTION = "馆方如何描述这件作品的笔触？"

# Keep these expectations independent of both the fixture and the implementation.
FROZEN_CASES = {
    "bedroom-q1": (BEDROOM, "这件作品用了什么材料？", "answered", [BEDROOM_MEDIUM]),
    "bedroom-q2": (
        BEDROOM,
        "馆方认为，画面里的哪些元素可能让人感到紧张？",
        "answered",
        [BEDROOM_DESCRIPTION],
    ),
    "bedroom-q3": (
        BEDROOM,
        "梵高画完芝加哥收藏的这一版，实际花了多少小时？",
        "insufficient",
        [],
    ),
    "toilette-q1": (TOILETTE, "这件作品用了什么材料？", "answered", [TOILETTE_MEDIUM]),
    "toilette-q2": (TOILETTE, BRUSHWORK_QUESTION, "answered", [TOILETTE_DESCRIPTION]),
    "toilette-q3": (TOILETTE, "画中女子的真实姓名是什么？", "insufficient", []),
}
ALIASES = (
    (
        (
            "material", "materials", "medium", "made of", "oil", "canvas",
            "材料", "材质", "媒介", "油画", "画布",
        ),
        BEDROOM_MEDIUM,
        TOILETTE_MEDIUM,
    ),
    (
        (
            "brushwork", "brushstroke", "brushstrokes", "brush stroke", "brush strokes",
            "brush-stroke", "brush-strokes", "笔触",
        ),
        BEDROOM_DESCRIPTION,
        TOILETTE_DESCRIPTION,
    ),
    (("nervous", "energy", "紧张"), BEDROOM_DESCRIPTION, None),
    (("color", "colors", "colour", "colours", "颜色", "色彩"), BEDROOM_DESCRIPTION, None),
    (("perspective", "receding lines", "透视"), BEDROOM_DESCRIPTION, None),
    (("background", "wallpaper", "bedspread", "背景", "墙纸", "床罩"), None, TOILETTE_DESCRIPTION),
)


def chunk_ids(response):
    return [hit["chunk_id"] for hit in response["evidence"]]


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(SEED_PATH)
        cls.seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        cls.evaluation = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
        cls.chunks = {chunk["chunk_id"]: chunk for chunk in cls.seed["chunks"]}
        cls.sources = {source["source_id"]: source for source in cls.seed["sources"]}

    def search(self, artwork_id, query, *, dataset_version=VERSION, **options):
        return retrieve(
            self.dataset,
            artwork_id=artwork_id,
            query=query,
            dataset_version=dataset_version,
            **options,
        )

    def assert_scoped_evidence(self, response, artwork_id, top_k=2):
        self.assertEqual(set(response), {"dataset_version", "evidence"})
        self.assertEqual(response["dataset_version"], VERSION)
        self.assertIsInstance(response["evidence"], list)
        ids = chunk_ids(response)
        self.assertLessEqual(len(ids), top_k)
        self.assertEqual(len(ids), len(set(ids)))
        for hit in response["evidence"]:
            chunk = self.chunks[hit["chunk_id"]]
            source = self.sources[chunk["source_id"]]
            self.assertEqual(chunk["artwork_id"], artwork_id)
            self.assertEqual(source["artwork_id"], artwork_id)
            expected = {key: value for key, value in chunk.items() if key != "topics"}
            for field in ("source_title", "source_url", "source_language"):
                expected[field] = source[field]
            self.assertEqual(hit, expected)

    def test_released_dataset_has_two_artworks_two_sources_and_four_chunks(self):
        self.assertEqual(self.dataset.dataset_version, VERSION)
        self.assertEqual(set(self.dataset.artworks), {BEDROOM, TOILETTE})
        self.assertEqual(set(self.dataset.sources), {"aic-api-28560", "aic-api-11723"})
        self.assertEqual(set(self.chunks), {
            BEDROOM_MEDIUM, BEDROOM_DESCRIPTION, TOILETTE_MEDIUM, TOILETTE_DESCRIPTION,
        })
        self.assertEqual(len(self.dataset.chunks), 4)

    def test_six_frozen_cases_retrieve_the_required_same_artwork_evidence(self):
        self.assertEqual(self.evaluation["dataset_version"], VERSION)
        cases = self.evaluation["cases"]
        self.assertEqual(len(cases), 6)
        self.assertEqual({case["case_id"] for case in cases}, set(FROZEN_CASES))

        exercised = set()
        for case in cases:
            case_id = case["case_id"]
            with self.subTest(case=case_id):
                artwork, question, status, required = FROZEN_CASES[case_id]
                self.assertEqual(case["artwork_id"], artwork)
                self.assertEqual(case["question"], question)
                self.assertEqual(case["answer_language"], "zh-CN")
                self.assertEqual(case["expected_status"], status)
                self.assertEqual(case["expected_chunk_ids"], required)

                response = self.search(artwork, question)
                self.assert_scoped_evidence(response, artwork)
                # An empty gold set describes answer support, not raw keyword hits.
                ids = chunk_ids(response)
                self.assertTrue(set(required).issubset(ids))
                exercised.update(ids)
        self.assertEqual(exercised, set(self.chunks))

    def test_frozen_brushwork_query_isolated_at_two_limits(self):
        checks = self.evaluation["cross_artwork_checks"]
        self.assertEqual(len(checks), 1)
        check = checks[0]
        self.assertEqual(check["check_id"], "same-query-brushwork")
        self.assertEqual(check["query"], BRUSHWORK_QUESTION)
        self.assertEqual(check["top_k"], 2)
        self.assertIs(check["require_all_results_match_artwork_id"], True)
        variants = check["variants"]
        self.assertEqual(len(variants), 2)
        expected = {BEDROOM: BEDROOM_DESCRIPTION, TOILETTE: TOILETTE_DESCRIPTION}
        self.assertEqual({variant["artwork_id"] for variant in variants}, set(expected))

        for variant in variants:
            artwork = variant["artwork_id"]
            expected_id = expected[artwork]
            with self.subTest(artwork=artwork):
                self.assertEqual(variant["expected_status"], "answered")
                self.assertEqual(variant["expected_chunk_ids"], [expected_id])
                self.assertEqual(variant["expected_first_chunk_id"], expected_id)
                for limit in (check["top_k"], 1):
                    response = self.search(artwork, check["query"], top_k=limit)
                    self.assert_scoped_evidence(response, artwork, limit)
                    self.assertEqual(chunk_ids(response)[:1], [expected_id])

    def test_all_documented_aliases_on_both_artworks(self):
        for aliases, bedroom, toilette in ALIASES:
            for alias in aliases:
                for artwork, expected in ((BEDROOM, bedroom), (TOILETTE, toilette)):
                    with self.subTest(alias=alias, artwork=artwork):
                        response = self.search(artwork, alias)
                        self.assertEqual(chunk_ids(response), [expected] if expected else [])

    def test_normalization_of_width_case_whitespace_and_punctuation(self):
        for query in (
            " What is this made of? ",
            "\u3000（ＷＨＡＴ　ＩＳ　ＴＨＩＳ　ＭＡＤＥ　ＯＦ？）\t",
            "What is this MADE\t\r\nOF?",
            "这件作品的材质是什么？",
        ):
            with self.subTest(query=query):
                self.assertEqual(chunk_ids(self.search(BEDROOM, query)), [BEDROOM_MEDIUM])

        self.assertEqual(
            self.search(TOILETTE, "（ＢＲＵＳＨＳＴＲＯＫＥＳ？）"),
            self.search(TOILETTE, "我想了解它的笔触。"),
        )

    def test_english_boundaries_and_no_stemming_or_fuzzy_matching(self):
        for query in (
            "immaterial",
            "pre_material",
            "canvases",
            "mediumship",
            "brushstroking",
            "brshwork",
            "nervousness",
            "colourful",
            "receding line",
        ):
            with self.subTest(query=query):
                self.assertEqual(chunk_ids(self.search(BEDROOM, query)), [])

        response = self.search(BEDROOM, "Describe the brushwork, please.")
        self.assertEqual(chunk_ids(response), [BEDROOM_DESCRIPTION])

    def test_contextual_phrases_retrieve_single_and_compound_evidence(self):
        cases = (
            (BEDROOM, "On which surface was this picture painted?", [BEDROOM_MEDIUM]),
            (BEDROOM, "Why does this scene feel so uneasy?", [BEDROOM_DESCRIPTION]),
            (TOILETTE, "What do the patterned shapes behind the sitter suggest?", [TOILETTE_DESCRIPTION]),
            (
                TOILETTE,
                "Which paint and support did she use, and what are the artist’s marks like?",
                [TOILETTE_DESCRIPTION, TOILETTE_MEDIUM],
            ),
            (
                BEDROOM,
                "这幅是画在什么东西上的？画面为什么显得不安？",
                [BEDROOM_DESCRIPTION, BEDROOM_MEDIUM],
            ),
            (TOILETTE, "她身后那些花纹看着像什么？", [TOILETTE_DESCRIPTION]),
            (BEDROOM, "Was the image painted on linen rather than paper?", [BEDROOM_MEDIUM]),
            (TOILETTE, "Do the marks left by the brush look delicate?", [TOILETTE_DESCRIPTION]),
            (
                TOILETTE,
                "Which objects in the scene seem to carry floral designs?",
                [TOILETTE_DESCRIPTION],
            ),
        )
        for artwork, query, expected in cases:
            with self.subTest(artwork=artwork, query=query):
                response = self.search(artwork, query)
                self.assert_scoped_evidence(response, artwork)
                self.assertEqual(chunk_ids(response), expected)

    def test_ambiguous_cues_do_not_match_without_local_context(self):
        for artwork, query in (
            (BEDROOM, "What was this painted on Tuesday for?"),
            (BEDROOM, "What support did the artist receive?"),
            (BEDROOM, "Did the artist feel uneasy? Where is the painting now?"),
            (BEDROOM, "Did the artist feel uneasy about this painting?"),
            (BEDROOM, "画家为什么不安？这幅画的构图是谁设计的？"),
            (BEDROOM, "画家看起来对这幅画的构图有些不安吗？"),
            (TOILETTE, "Who stood behind the sitter in real life?"),
            (TOILETTE, "她身后站的是谁？"),
        ):
            with self.subTest(artwork=artwork, query=query):
                self.assertEqual(chunk_ids(self.search(artwork, query)), [])

    def test_unknown_topics_and_other_artwork_topics_do_not_fall_back(self):
        for artwork, query in (
            (BEDROOM, "zither"),
            (BEDROOM, "Tell me about the wallpaper in Woman at Her Toilette."),
            (TOILETTE, "Tell me about the nervous energy in The Bedroom."),
        ):
            with self.subTest(artwork=artwork, query=query):
                self.assertEqual(
                    self.search(artwork, query),
                    {"dataset_version": VERSION, "evidence": []},
                )

    def test_explicit_work_references_preserve_the_selected_scope(self):
        for artwork, query, expected in (
            (BEDROOM, "Describe the brushwork in Woman at Her Toilette.", []),
            (TOILETTE, "Describe the brushwork in The Bedroom.", []),
            (
                BEDROOM,
                "What is the medium of Woman at Her Toilette, and how does its brushwork look?",
                [],
            ),
            (TOILETTE, "《卧室》的媒材是什么？那幅画的笔触呢？", []),
            (
                BEDROOM,
                "Compare the brushwork in Woman at Her Toilette with Morisot's other works.",
                [],
            ),
            (TOILETTE, "《卧室》的笔触是什么样的？", []),
            (TOILETTE, "Can you describe the wallpaper in the bedroom?", [TOILETTE_DESCRIPTION]),
            (TOILETTE, "Describe the brushwork in Woman at Her Toilette.", [TOILETTE_DESCRIPTION]),
            (
                BEDROOM,
                "How do the strokes in this painting compare with the brushwork in Woman at Her Toilette?",
                [BEDROOM_DESCRIPTION],
            ),
            (
                TOILETTE,
                "这幅作品的笔触和《卧室》的笔触有何区别？",
                [TOILETTE_DESCRIPTION],
            ),
            (
                BEDROOM,
                "Describe the brushwork in Woman at Her Toilette. What is the medium here?",
                [BEDROOM_MEDIUM],
            ),
        ):
            with self.subTest(artwork=artwork, query=query):
                response = self.search(artwork, query)
                self.assert_scoped_evidence(response, artwork)
                self.assertEqual(chunk_ids(response), expected)

    def test_unsupported_details_do_not_promote_context_words(self):
        for artwork, query in (
            (BEDROOM, "Which oil did the painter mix with the pigments?"),
            (BEDROOM, "这幅油画的颜料，是用什么油调的？"),
            (BEDROOM, "Was the canvas relined during conservation?"),
            (BEDROOM, "What is the actual size of the canvas?"),
            (TOILETTE, "Did she use a narrow brush for this brushwork?"),
            (TOILETTE, "画出这些笔触的时候，画笔的尺寸是多少？"),
            (TOILETTE, "Did she model the wallpaper on an actual room?"),
            (TOILETTE, "她是不是等颜料干透之后，才画的墙纸？"),
            (BEDROOM, "Did Van Gogh intend the colors to make visitors uneasy?"),
            (BEDROOM, "Do the receding lines reproduce the room's exact dimensions?"),
            (BEDROOM, "How many marks left by the brush look broken?"),
            (TOILETTE, "When was it painted on paper before it was moved to cloth?"),
        ):
            with self.subTest(artwork=artwork, query=query):
                self.assertEqual(chunk_ids(self.search(artwork, query)), [])

    def test_supported_parts_survive_unsupported_questions_on_the_same_topic(self):
        for artwork, query, expected in (
            (
                BEDROOM,
                "What is the medium and was the canvas later trimmed?",
                BEDROOM_MEDIUM,
            ),
            (
                BEDROOM,
                "这幅画用了什么材料，画布后来有裁过吗？",
                BEDROOM_MEDIUM,
            ),
            (BEDROOM, "What are the medium and actual dimensions?", BEDROOM_MEDIUM),
            (BEDROOM, "What are the actual dimensions and medium of this artwork?", BEDROOM_MEDIUM),
            (BEDROOM, "Are those thick brush marks?", BEDROOM_DESCRIPTION),
            (
                TOILETTE,
                "馆方怎么形容这些笔触，又是用大画笔还是小画笔画出这些笔触的呢？",
                TOILETTE_DESCRIPTION,
            ),
            (
                TOILETTE,
                "Which objects seem to carry the floral pattern in this scene, and when was the pattern painted?",
                TOILETTE_DESCRIPTION,
            ),
        ):
            with self.subTest(artwork=artwork, query=query):
                response = self.search(artwork, query)
                self.assert_scoped_evidence(response, artwork)
                self.assertEqual(chunk_ids(response), [expected])

    def test_score_uses_distinct_topics_and_limit_applies_after_sorting(self):
        # Multiple aliases and repeats still contribute just one material topic.
        query = "oil, canvas, material, oil; brushwork and colors"
        for limit, expected in (
            (1, [BEDROOM_DESCRIPTION]),
            (2, [BEDROOM_DESCRIPTION, BEDROOM_MEDIUM]),
            (5, [BEDROOM_DESCRIPTION, BEDROOM_MEDIUM]),
        ):
            with self.subTest(top_k=limit):
                response = self.search(BEDROOM, query, top_k=limit)
                self.assert_scoped_evidence(response, BEDROOM, limit)
                self.assertEqual(chunk_ids(response), expected)
        self.assertEqual(
            chunk_ids(self.search(BEDROOM, query)),
            [BEDROOM_DESCRIPTION, BEDROOM_MEDIUM],
        )

    def test_tie_break_uses_chunk_id_even_if_input_order_changes(self):
        query = "material and brushwork"
        expected = [BEDROOM_DESCRIPTION, BEDROOM_MEDIUM]
        self.assertEqual(chunk_ids(self.search(BEDROOM, query)), expected)

        # Force the larger ID to appear before the smaller ID in the seed.
        seed = dict(self.seed, chunks=sorted(
            self.seed["chunks"], key=lambda chunk: chunk["chunk_id"], reverse=True,
        ))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / SEED_PATH.name
            path.write_text(json.dumps(seed), encoding="utf-8")
            dataset = load_dataset(path)
            response = retrieve(dataset, artwork_id=BEDROOM, query=query, dataset_version=VERSION)
        self.assertEqual(chunk_ids(response), expected)

    def test_request_trace_binds_medium_to_the_paint_question_only(self):
        query = "这块画布有多大？上面用的是哪一类颜料？"
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=BEDROOM, query=query, dataset_version=VERSION,
        )
        self.assertEqual(result, self.search(BEDROOM, query))
        self.assertEqual(chunk_ids(result), [BEDROOM_MEDIUM])
        requests = {request["facet"]: request for request in trace["requests"]}
        self.assertEqual(set(requests), {"dimensions", "paint_medium"})
        self.assertEqual(requests["dimensions"]["chunk_ids"], [])
        self.assertEqual(requests["dimensions"]["status"], "insufficient_evidence")
        self.assertEqual(requests["paint_medium"]["chunk_ids"], [BEDROOM_MEDIUM])
        self.assertEqual(requests["paint_medium"]["status"], "supported")
        for name, expected in (("dimensions", "多大"), ("paint_medium", "颜料")):
            request = requests[name]
            self.assertIn(expected, trace["normalized_query"][request["start"]:request["end"]])

    def test_trace_keeps_two_material_requests_but_one_evidence_record(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=TOILETTE,
            query="What kind of paint and surface did the artist use?",
            dataset_version=VERSION,
        )
        self.assertEqual(chunk_ids(result), [TOILETTE_MEDIUM])
        self.assertEqual(
            {r["facet"]: r["chunk_ids"] for r in trace["requests"]},
            {"paint_medium": [TOILETTE_MEDIUM], "painting_support": [TOILETTE_MEDIUM]},
        )

    def test_trace_records_the_foreign_comparison_side_as_out_of_scope(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=TOILETTE,
            query="How does the brushwork here compare with the brushwork in The Bedroom?",
            dataset_version=VERSION,
        )
        self.assertEqual(chunk_ids(result), [TOILETTE_DESCRIPTION])
        by_artwork = {r["artwork_id"]: r for r in trace["requests"]}
        self.assertEqual(set(by_artwork), {TOILETTE, BEDROOM})
        self.assertEqual(by_artwork[BEDROOM]["status"], "out_of_scope")
        self.assertEqual(by_artwork[BEDROOM]["chunk_ids"], [])
        self.assertEqual(by_artwork[TOILETTE]["chunk_ids"], [TOILETTE_DESCRIPTION])

    def test_trace_keeps_indirect_request_but_does_not_query_its_premise(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=TOILETTE, dataset_version=VERSION,
            query="The label says oil on canvas. I wonder if the layer was still wet",
        )
        self.assertEqual(chunk_ids(result), [])
        self.assertEqual(
            [(r["facet"], r["status"]) for r in trace["requests"]],
            [("painting_process", "insufficient_evidence")],
        )

    def test_dependent_background_request_keeps_the_named_subject(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=BEDROOM, dataset_version=VERSION,
            query="I was looking behind the sitter in Woman at Her Toilette. "
                  "Do those brushstrokes look delicate?",
        )
        self.assertEqual(chunk_ids(result), [])
        self.assertEqual(
            [(r["artwork_id"], r["status"]) for r in trace["requests"]],
            [(TOILETTE, "out_of_scope")],
        )

    def test_question_about_a_support_does_not_also_request_the_paint_family(self):
        _, trace = retrieve_with_trace(
            self.dataset, artwork_id=BEDROOM, dataset_version=VERSION,
            query="What did the artist paint on?",
        )
        self.assertEqual([r["facet"] for r in trace["requests"]], ["painting_support"])

    def test_equal_medium_comparison_preserves_both_requested_work_ids(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=TOILETTE, dataset_version=VERSION,
            query="Is the medium the same in The Bedroom and Woman at Her Toilette?",
        )
        self.assertEqual(chunk_ids(result), [TOILETTE_MEDIUM])
        self.assertEqual(
            {(r["artwork_id"], r["facet"], r["status"]) for r in trace["requests"]},
            {(BEDROOM, "medium", "out_of_scope"), (TOILETTE, "medium", "supported")},
        )

    def test_context_can_resolve_a_description_without_producing_its_own_request(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=BEDROOM, dataset_version=VERSION,
            query="The palette caught my eye. How does the museum characterize it?",
        )
        self.assertEqual(chunk_ids(result), [BEDROOM_DESCRIPTION])
        self.assertEqual(
            [(r["facet"], r["chunk_ids"]) for r in trace["requests"]],
            [("color_appearance", [BEDROOM_DESCRIPTION])],
        )

    def test_typed_line_context_survives_an_intervening_medium_question(self):
        for query in (
            "The lines of perspective in The Bedroom caught my eye. "
            "What is the medium? How steeply do they run into the distance?",
            "我先注意到《卧室》里的透视线。媒材是什么？它们朝纵深延伸得有多急？",
        ):
            with self.subTest(query=query):
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=BEDROOM, query=query, dataset_version=VERSION,
                )
                self.assertCountEqual(chunk_ids(result), [BEDROOM_MEDIUM, BEDROOM_DESCRIPTION])
                self.assertEqual(
                    [(r["facet"], r["chunk_ids"]) for r in trace["requests"]],
                    [("medium", [BEDROOM_MEDIUM]), ("line_recession", [BEDROOM_DESCRIPTION])],
                )

    def test_generic_reference_abstains_when_more_than_one_aspect_could_fit(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=BEDROOM, dataset_version=VERSION,
            query="The brushstrokes and palette both caught my attention. "
                  "How does the curator describe them?",
        )
        self.assertEqual(chunk_ids(result), [])
        self.assertEqual(
            [(r["facet"], r["status"]) for r in trace["requests"]],
            [("unknown", "unknown_intent")],
        )

    def test_explicit_current_scope_does_not_reuse_foreign_aspect_context(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=TOILETTE, dataset_version=VERSION,
            query="The perspective lines in The Bedroom caught my eye. "
                  "How do they run into the distance? "
                  "How does the museum characterize them in this painting? "
                  "How is the brushwork here described?",
        )
        self.assertEqual(chunk_ids(result), [TOILETTE_DESCRIPTION])
        self.assertEqual(
            [(r["artwork_id"], r["facet"], r["status"]) for r in trace["requests"]],
            [
                (BEDROOM, "line_recession", "out_of_scope"),
                (TOILETTE, "unknown", "unknown_intent"),
                (TOILETTE, "brushwork_appearance", "supported"),
            ],
        )

    def test_pigment_identity_does_not_use_palette_or_medium_evidence(self):
        for query in (
            "Which actual pigments account for the visible colors?",
            "画面这些颜色是用什么具体颜料调出来的？",
        ):
            with self.subTest(query=query):
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=BEDROOM, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [])
                self.assertEqual(
                    [(r["facet"], r["status"]) for r in trace["requests"]],
                    [("pigment_identity", "insufficient_evidence")],
                )

    def test_visible_colors_and_paint_family_survive_a_pigment_question(self):
        for query in (
            "How would you describe the colors, and which pigments account for them? "
            "What kind of paint was used overall?",
            "画里的色彩有什么特点？这些颜色是用什么具体颜料调出来的？"
            "整幅画用的是哪种类型的绘画颜料？",
        ):
            with self.subTest(query=query):
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=BEDROOM, query=query, dataset_version=VERSION,
                )
                self.assertCountEqual(chunk_ids(result), [BEDROOM_DESCRIPTION, BEDROOM_MEDIUM])
                self.assertEqual(
                    [(r["facet"], r["chunk_ids"]) for r in trace["requests"]],
                    [
                        ("color_appearance", [BEDROOM_DESCRIPTION]),
                        ("pigment_identity", []),
                        ("paint_medium", [BEDROOM_MEDIUM]),
                    ],
                )

    def test_unqualified_premise_does_not_rebind_another_artwork(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=BEDROOM, dataset_version=VERSION,
            query="I am looking at Woman at Her Toilette. The brushstrokes caught my eye. "
                  "Can you describe them?",
        )
        self.assertEqual(chunk_ids(result), [])
        self.assertEqual(
            [(r["artwork_id"], r["facet"], r["status"]) for r in trace["requests"]],
            [(TOILETTE, "brushwork_appearance", "out_of_scope")],
        )

    def test_typed_context_does_not_treat_history_as_spatial_recession(self):
        result = self.search(BEDROOM, "The perspective lines caught my eye. Do they go back to 1888?")
        self.assertEqual(chunk_ids(result), [])

    def test_composition_predicate_can_refer_to_the_palette_but_not_to_the_frame(self):
        for query in (
            "The colors caught my attention. What were they made from?",
            "画里的色彩很抢眼。它们是用什么调配出来的？",
            "What are the colors made from?",
            "画中的色彩是用什么调出来的？",
        ):
            with self.subTest(query=query):
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=BEDROOM, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [])
                self.assertEqual(
                    [(r["facet"], r["status"]) for r in trace["requests"]],
                    [("pigment_identity", "insufficient_evidence")],
                )
        result = self.search(BEDROOM, "How are the colors described in the picture with a frame made from wood?")
        self.assertEqual(chunk_ids(result), [BEDROOM_DESCRIPTION])

    def test_botanical_identity_does_not_retrieve_the_floral_pattern(self):
        for query in (
            "Can you tell which genus the blossoms in the wallpaper belong to?",
            "墙纸上的花朵能辨认出具体品种吗？",
        ):
            with self.subTest(query=query):
                self.assertEqual(chunk_ids(self.search(TOILETTE, query)), [])
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=TOILETTE, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [])
                self.assertEqual(
                    [(r["facet"], r["status"], r["chunk_ids"]) for r in trace["requests"]],
                    [("flower_species", "insufficient_evidence", [])],
                )

    def test_compound_query_separates_floral_motif_from_species(self):
        for query in (
            "What objects in the background have a floral pattern, "
            "and does the label give the botanical name of the flowers?",
            "背景里哪些东西有花卉图案？这些花朵的学名是什么？",
        ):
            with self.subTest(query=query):
                self.assertEqual(chunk_ids(self.search(TOILETTE, query)), [TOILETTE_DESCRIPTION])
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=TOILETTE, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [TOILETTE_DESCRIPTION])
                self.assertEqual(
                    [(r["facet"], r["status"], r["chunk_ids"]) for r in trace["requests"]],
                    [
                        ("background_objects", "supported", [TOILETTE_DESCRIPTION]),
                        ("flower_species", "insufficient_evidence", []),
                    ],
                )

    def test_botanical_identity_follow_up_needs_a_plant_referent(self):
        for query, facet, status in (
            (
                "I noticed the flowers in the wallpaper design. Which species are they?",
                "flower_species", "insufficient_evidence",
            ),
            (
                "墙纸纹样中的花朵很醒目。它们属于什么品种？",
                "flower_species", "insufficient_evidence",
            ),
            (
                "The blossoms are quite distinct. Can the label identify them?",
                "flower_species", "insufficient_evidence",
            ),
            (
                "画里的花朵很醒目。能辨认出它们吗？",
                "flower_species", "insufficient_evidence",
            ),
            (
                "The flowers and brush marks catch my eye. Can you identify them?",
                "unknown", "unknown_intent",
            ),
            (
                "花朵和笔触都很醒目。能辨认出它们吗？",
                "unknown", "unknown_intent",
            ),
            (
                "I noticed the shapes in the wallpaper design. Which species are they?",
                "unknown", "unknown_intent",
            ),
            (
                "墙纸纹样中的图形很醒目。它们属于什么品种？",
                "unknown", "unknown_intent",
            ),
        ):
            with self.subTest(query=query):
                self.assertEqual(chunk_ids(self.search(TOILETTE, query)), [])
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=TOILETTE, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [])
                self.assertEqual(
                    [(r["facet"], r["status"], r["chunk_ids"]) for r in trace["requests"]],
                    [(facet, status, [])],
                )

    def test_generic_pattern_and_material_type_are_not_botanical_identity(self):
        for query, facet, evidence_id in (
            ("What type of motif appears on the wallpaper?", "background_objects", TOILETTE_DESCRIPTION),
            ("墙纸上是什么样的纹样？", "background_objects", TOILETTE_DESCRIPTION),
            ("What kind of paint was used on this canvas?", "paint_medium", TOILETTE_MEDIUM),
            ("用的是哪一类绘画颜料？", "paint_medium", TOILETTE_MEDIUM),
        ):
            with self.subTest(query=query):
                self.assertEqual(chunk_ids(self.search(TOILETTE, query)), [evidence_id])
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=TOILETTE, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [evidence_id])
                self.assertEqual(
                    [(r["facet"], r["status"], r["chunk_ids"]) for r in trace["requests"]],
                    [(facet, "supported", [evidence_id])],
                )

    def test_mark_appearance_and_implied_objects_have_separate_bindings(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=TOILETTE, dataset_version=VERSION,
            query="What do the marks behind the sitter look like, and what do they suggest might be further back?",
        )
        self.assertEqual(chunk_ids(result), [TOILETTE_DESCRIPTION])
        self.assertEqual(
            [(r["facet"], r["chunk_ids"]) for r in trace["requests"]],
            [
                ("brushwork_appearance", [TOILETTE_DESCRIPTION]),
                ("background_objects", [TOILETTE_DESCRIPTION]),
            ],
        )

    def test_referential_appearance_requires_a_relevant_antecedent(self):
        for artwork, query in (
            (BEDROOM, "The colors catch my eye. I noticed the frame too. How does the museum describe its appearance?"),
            (TOILETTE, "What does the appearance of the marks on the reverse tell us?"),
            (TOILETTE, "I notice brushstrokes behind the sitter. Do they suggest she worked quickly?"),
            (TOILETTE, "这幅画上有花卉纹样。能认出它们吗？"),
        ):
            with self.subTest(query=query):
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=artwork, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [])
                self.assertEqual([r["facet"] for r in trace["requests"]], ["unknown"])

    def test_taxonomic_detail_in_a_floral_motif_has_no_species_evidence(self):
        for query in (
            "Does the label give the genus depicted in the floral motif?",
            "墙纸的花卉图案画的是哪个属种？",
        ):
            with self.subTest(query=query):
                result, trace = retrieve_with_trace(
                    self.dataset, artwork_id=TOILETTE, query=query, dataset_version=VERSION,
                )
                self.assertEqual(chunk_ids(result), [])
                self.assertEqual(
                    [(r["facet"], r["status"]) for r in trace["requests"]],
                    [("flower_species", "insufficient_evidence")],
                )

    def test_equal_medium_can_compare_an_explicit_title_to_the_current_work(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=TOILETTE, dataset_version=VERSION,
            query="Is the medium the same for this picture and The Bedroom?",
        )
        self.assertEqual(chunk_ids(result), [TOILETTE_MEDIUM])
        self.assertEqual(
            {(r["artwork_id"], r["status"]) for r in trace["requests"]},
            {(BEDROOM, "out_of_scope"), (TOILETTE, "supported")},
        )

    def test_trace_cannot_claim_a_chunk_discarded_by_the_global_limit(self):
        result, trace = retrieve_with_trace(
            self.dataset, artwork_id=BEDROOM, top_k=1,
            query="What is the medium, and how does the museum describe the brushwork and colors?",
            dataset_version=VERSION,
        )
        self.assertEqual(chunk_ids(result), [BEDROOM_DESCRIPTION])
        by_facet = {r["facet"]: r["chunk_ids"] for r in trace["requests"]}
        self.assertEqual(by_facet["medium"], [])
        self.assertEqual(by_facet["brushwork_appearance"], [BEDROOM_DESCRIPTION])
        self.assertEqual(by_facet["color_appearance"], [BEDROOM_DESCRIPTION])

    def test_dataset_collections_are_immutable(self):
        self.assertIsInstance(self.dataset.chunks, tuple)
        with self.assertRaises((AttributeError, TypeError)):
            self.dataset.dataset_version = "other"
        with self.assertRaises(TypeError):
            self.dataset.artworks[BEDROOM] = {}
        with self.assertRaises(TypeError):
            self.dataset.sources["aic-api-28560"] = {}
        artwork = self.dataset.artworks[BEDROOM]
        source = self.dataset.sources["aic-api-28560"]
        chunk = self.dataset.chunks[0]
        self.assertEqual(artwork["title"], "The Bedroom")
        self.assertEqual(source["artwork_id"], BEDROOM)
        self.assertIsInstance(chunk["topics"], tuple)
        with self.assertRaises(TypeError):
            artwork["title"] = "changed by caller"
        with self.assertRaises(TypeError):
            source["source_url"] = "changed by caller"
        with self.assertRaises(TypeError):
            chunk["excerpt"] = "changed by caller"

    def test_results_are_repeatable_and_isolated_from_the_dataset(self):
        query = "material and brushwork"
        result = self.search(BEDROOM, query)
        untouched = self.search(BEDROOM, query)
        self.assertEqual(result, untouched)

        result["evidence"][0]["excerpt"] = "changed by caller"
        result["evidence"].pop()
        result["dataset_version"] = "changed by caller"
        self.assertNotEqual(result, untouched)
        self.assertEqual(self.search(BEDROOM, query), untouched)
        self.assert_scoped_evidence(untouched, BEDROOM)

    def test_invalid_top_k_is_a_typed_error(self):
        for top_k in (False, True, 2.0, 2.5, "2", None, 0, -1, 6):
            with self.subTest(top_k=top_k):
                with self.assertRaises(RetrievalError) as error:
                    self.search(BEDROOM, "material", top_k=top_k)
                self.assertIsInstance(error.exception, ValueError)
                self.assertEqual(error.exception.code, "invalid_request")

    def test_query_limit_is_trimmed_unicode_code_points(self):
        query = "material " + "🎨" * 991
        self.assertEqual(len(query), 1000)
        response = self.search(BEDROOM, f"\t{query}\n")
        self.assertEqual(chunk_ids(response), [BEDROOM_MEDIUM])

        with self.assertRaises(RetrievalError) as error:
            self.search(BEDROOM, query + "🎨")
        self.assertEqual(error.exception.code, "invalid_request")

    def test_invalid_query_is_a_typed_error(self):
        for query in (
            "", "\t \n\u3000", None, 123, b"material", ["material"],
            "material\x00canvas", "material\x0bcanvas", "material\x7fcanvas",
            "material\ud800canvas", "material\udfffcanvas",
        ):
            with self.subTest(query=query):
                with self.assertRaises(RetrievalError) as error:
                    self.search(BEDROOM, query)
                self.assertEqual(error.exception.code, "invalid_request")

    def test_unknown_artwork_and_version_have_distinct_errors(self):
        for artwork, version, code in (
            ("aic-00000", VERSION, "artwork_not_found"),
            ("AIC-28560", VERSION, "artwork_not_found"),
            (BEDROOM, "aic-seed-v0.2", "dataset_version_mismatch"),
            (None, VERSION, "invalid_request"),
            ("", VERSION, "invalid_request"),
            ("  ", VERSION, "invalid_request"),
            (BEDROOM, "", "invalid_request"),
            (BEDROOM, "\t ", "invalid_request"),
            (BEDROOM, 1, "invalid_request"),
        ):
            with self.subTest(artwork=artwork, version=version):
                with self.assertRaises(RetrievalError) as error:
                    self.search(artwork, "material", dataset_version=version)
                self.assertEqual(error.exception.code, code)

    def test_unknown_request_keyword_is_not_accepted(self):
        with self.assertRaises(TypeError):
            self.search(BEDROOM, "material", conversation_id="not-retrieval-input")


if __name__ == "__main__":
    unittest.main()
