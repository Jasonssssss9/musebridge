package catalog

// Origin is the minimal released source metadata needed for catalog citations.
// It exposes no passages and is detached from the dataset's internal maps.
type Origin struct {
	SourceID    string
	SourceTitle string
	SourceURL   string
	Institution string
}

// ArtworkOrigin selects the actual released source association, rather than
// deriving provenance from a chunk name or a chunk's attribution.
func (d *Dataset) ArtworkOrigin(artworkID string) (Origin, bool) {
	artwork, found := d.ArtworkBySlug(artworkID)
	if !found {
		return Origin{}, false
	}
	var result Origin
	count := 0
	for _, source := range d.sources {
		if source.ArtworkID == artworkID && source.SourceURL == artwork.SourceURL && source.SourceKind != "guide_note" {
			result = Origin{source.SourceID, source.SourceTitle, source.SourceURL, source.Institution}
			count++
		}
	}
	return result, count == 1
}
