package catalog

import "testing"

func TestArtworkOriginUsesReleasedAssociationAndDetachedValues(t *testing.T) {
	dataset, err := Load("../../../knowledge/data/aic-seed-v0.1.json", "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"aic-28560", "aic-11723"} {
		origin, ok := dataset.ArtworkOrigin(id)
		if !ok {
			t.Fatal("missing origin", id)
		}
		original := origin
		origin.SourceTitle = "caller mutation"
		again, _ := dataset.ArtworkOrigin(id)
		if again != original {
			t.Fatal("mutable origin escaped")
		}
		// Source institution, rather than any chunk attribution, owns catalog credit.
		source := dataset.sources[original.SourceID]
		source.Institution = "Reviewed source institution"
		dataset.sources[original.SourceID] = source
		changed, _ := dataset.ArtworkOrigin(id)
		if changed.Institution != source.Institution {
			t.Fatal("guessed attribution from chunks")
		}
	}
	if _, ok := dataset.ArtworkOrigin("unknown"); ok {
		t.Fatal("unknown origin accepted")
	}
}
