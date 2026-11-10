package catalog

// Evidence is the trusted chunk/source projection shared by retrieval and
// citations. Every field is a string, so returned values contain no shared maps.
type Evidence struct {
	ChunkID        string `json:"chunk_id"`
	ArtworkID      string `json:"artwork_id"`
	SourceID       string `json:"source_id"`
	SourceTitle    string `json:"source_title"`
	SourceURL      string `json:"source_url"`
	SourceLanguage string `json:"source_language"`
	SourceLocator  string `json:"source_locator"`
	Excerpt        string `json:"excerpt"`
	License        string `json:"license"`
	LicenseURL     string `json:"license_url"`
	Attribution    string `json:"attribution"`
	Normalization  string `json:"normalization"`
}

// EvidenceByChunkID returns a new value from the locally validated release.
// It does not rank evidence or contact the retriever.
func (d *Dataset) EvidenceByChunkID(id string) (Evidence, bool) {
	item, exists := d.chunks[id]
	if !exists {
		return Evidence{}, false
	}
	origin := d.sources[item.SourceID]
	return Evidence{
		ChunkID: item.ChunkID, ArtworkID: item.ArtworkID, SourceID: item.SourceID,
		SourceTitle: origin.SourceTitle, SourceURL: origin.SourceURL, SourceLanguage: origin.SourceLanguage,
		SourceLocator: item.SourceLocator, Excerpt: item.Excerpt,
		License: item.License, LicenseURL: item.LicenseURL,
		Attribution: item.Attribution, Normalization: item.Normalization,
	}, true
}
