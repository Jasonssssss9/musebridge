package catalog

import "testing"

func TestEvidenceProjectionIsCompleteAndIsolated(t *testing.T) {
	dataset, err := Load(releasedSeed, "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	for id, item := range dataset.chunks {
		evidence, found := dataset.EvidenceByChunkID(id)
		origin := dataset.sources[item.SourceID]
		if !found || evidence != (Evidence{
			ChunkID: id, ArtworkID: item.ArtworkID, SourceID: item.SourceID,
			SourceTitle: origin.SourceTitle, SourceURL: origin.SourceURL, SourceLanguage: origin.SourceLanguage,
			SourceLocator: item.SourceLocator, Excerpt: item.Excerpt,
			License: item.License, LicenseURL: item.LicenseURL,
			Attribution: item.Attribution, Normalization: item.Normalization,
		}) {
			t.Fatalf("incorrect trusted projection for %s", id)
		}
		evidence.Excerpt = "modified response"
		again, _ := dataset.EvidenceByChunkID(id)
		if again.Excerpt != item.Excerpt {
			t.Fatal("a caller changed the loaded provenance")
		}
	}
	if _, found := dataset.EvidenceByChunkID("unknown"); found {
		t.Fatal("unknown evidence was fabricated")
	}
}
