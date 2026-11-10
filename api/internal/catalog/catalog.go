// Package catalog loads the shared, immutable artwork and evidence release.
package catalog

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/Jasonssssss9/musebridge/api/internal/jsonwire"
)

const institution = "Art Institute of Chicago"

var utcTimestamp = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)$`)

// Artwork is the public artwork card defined by local-demo-v0.1.
type Artwork struct {
	ArtworkID       string  `json:"artwork_id"`
	Slug            string  `json:"slug"`
	Title           string  `json:"title"`
	Artist          string  `json:"artist"`
	DateDisplay     string  `json:"date_display"`
	Museum          string  `json:"museum"`
	MuseumRecordID  int     `json:"museum_record_id"`
	AccessionNumber string  `json:"accession_number"`
	SourceURL       string  `json:"source_url"`
	ImageURL        *string `json:"image_url"`
	DatasetVersion  string  `json:"dataset_version"`
}

type source struct {
	SourceID       string `json:"source_id"`
	ArtworkID      string `json:"artwork_id"`
	SourceTitle    string `json:"source_title"`
	SourceURL      string `json:"source_url"`
	SourceLanguage string `json:"source_language"`
	Institution    string `json:"institution"`
	RetrievedAt    string `json:"retrieved_at"`
	TermsURL       string `json:"terms_url"`
}

type chunk struct {
	ChunkID       string   `json:"chunk_id"`
	ArtworkID     string   `json:"artwork_id"`
	SourceID      string   `json:"source_id"`
	SourceLocator string   `json:"source_locator"`
	Excerpt       string   `json:"excerpt"`
	License       string   `json:"license"`
	LicenseURL    string   `json:"license_url"`
	Attribution   string   `json:"attribution"`
	Normalization string   `json:"normalization"`
	Topics        []string `json:"topics"`
}

type seedFile struct {
	DatasetVersion string    `json:"dataset_version"`
	Artworks       []Artwork `json:"artworks"`
	Sources        []source  `json:"sources"`
	Chunks         []chunk   `json:"chunks"`
}

// Dataset has no mutating methods and never exposes its internal collections.
// Sources and chunks are retained for later retrieval-response validation.
type Dataset struct {
	version  string
	checksum string
	artworks map[string]Artwork
	sources  map[string]source
	chunks   map[string]chunk
}

func (d *Dataset) Version() string  { return d.version }
func (d *Dataset) Checksum() string { return d.checksum }

// ArtworkBySlug returns a value copy. This release permits no image pointers.
func (d *Dataset) ArtworkBySlug(slug string) (Artwork, bool) {
	artwork, ok := d.artworks[slug]
	return artwork, ok
}

// Load validates the full shared seed before exposing any artwork. It performs
// no network calls and does not substitute an empty dataset on failure.
func Load(path, expectedVersion string) (*Dataset, error) {
	if !validText(expectedVersion) {
		return nil, fmt.Errorf("the expected dataset version must be a nonempty string")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read seed: %w", err)
	}
	if err := jsonwire.Validate(data); err != nil {
		return nil, fmt.Errorf("invalid seed JSON: %w", err)
	}
	root, err := objectFields(data, "dataset_version artworks sources chunks")
	if err != nil {
		return nil, err
	}
	shapes := map[string]string{
		"artworks": "artwork_id slug title artist date_display museum museum_record_id accession_number source_url image_url dataset_version",
		"sources":  "source_id artwork_id source_title source_url source_language institution retrieved_at terms_url",
		"chunks":   "chunk_id artwork_id source_id source_locator excerpt license license_url attribution normalization topics",
	}
	for name, fields := range shapes {
		var records []json.RawMessage
		if err := json.Unmarshal(root[name], &records); err != nil {
			return nil, fmt.Errorf("%s must be an array: %w", name, err)
		}
		for index, record := range records {
			if _, err := objectFields(record, fields); err != nil {
				return nil, fmt.Errorf("%s[%d]: %w", name, index, err)
			}
		}
	}
	var seed seedFile
	if err := json.Unmarshal(data, &seed); err != nil {
		return nil, fmt.Errorf("invalid seed field type: %w", err)
	}
	if seed.DatasetVersion != expectedVersion {
		return nil, fmt.Errorf("seed version does not match DATASET_VERSION")
	}
	if len(seed.Artworks) != 2 || len(seed.Sources) != 2 || len(seed.Chunks) != 4 {
		return nil, fmt.Errorf("this release requires two artworks, two sources and four chunks")
	}
	dataset := &Dataset{
		version: seed.DatasetVersion, checksum: fmt.Sprintf("%x", sha256.Sum256(data)),
		artworks: make(map[string]Artwork), sources: make(map[string]source), chunks: make(map[string]chunk),
	}
	for _, artwork := range seed.Artworks {
		if (artwork.ArtworkID != "aic-28560" && artwork.ArtworkID != "aic-11723") ||
			artwork.ArtworkID != fmt.Sprintf("aic-%d", artwork.MuseumRecordID) ||
			artwork.Slug != artwork.ArtworkID || artwork.DatasetVersion != seed.DatasetVersion ||
			artwork.Museum != institution || artwork.ImageURL != nil ||
			artwork.SourceURL != fmt.Sprintf("https://api.artic.edu/api/v1/artworks/%d", artwork.MuseumRecordID) {
			return nil, fmt.Errorf("artwork metadata violates the released catalog")
		}
		if _, exists := dataset.artworks[artwork.Slug]; exists {
			return nil, fmt.Errorf("duplicate artwork ID or slug")
		}
		dataset.artworks[artwork.Slug] = artwork
	}
	for _, item := range seed.Sources {
		artwork, exists := dataset.artworks[item.ArtworkID]
		fetched, timeErr := time.Parse(time.RFC3339Nano, item.RetrievedAt)
		utc := utcTimestamp.MatchString(item.RetrievedAt)
		if !exists || item.SourceID != fmt.Sprintf("aic-api-%d", artwork.MuseumRecordID) ||
			item.SourceTitle != artwork.Title+" — Art Institute of Chicago API record" ||
			item.SourceURL != artwork.SourceURL || item.SourceLanguage != "en" ||
			item.Institution != institution || item.TermsURL != "https://www.artic.edu/terms" ||
			timeErr != nil || !utc || fetched.Year() < 1 {
			return nil, fmt.Errorf("source metadata or UTC fetch time violates the released catalog")
		}
		if _, exists := dataset.sources[item.SourceID]; exists {
			return nil, fmt.Errorf("duplicate source ID")
		}
		dataset.sources[item.SourceID] = item
	}
	expectedTopics := map[string][]string{
		"aic-28560-medium": {"material"}, "aic-11723-medium": {"material"},
		"aic-28560-description-01": {"brushwork", "energy", "color", "perspective"},
		"aic-11723-description-01": {"brushwork", "background"},
	}
	for _, item := range seed.Chunks {
		origin, sourceExists := dataset.sources[item.SourceID]
		_, artworkExists := dataset.artworks[item.ArtworkID]
		topics, knownChunk := expectedTopics[item.ChunkID]
		if !sourceExists || !artworkExists || !knownChunk || origin.ArtworkID != item.ArtworkID ||
			utf8.RuneCountInString(item.Excerpt) > 4000 || !sameTopics(item.Topics, topics) {
			return nil, fmt.Errorf("invalid chunk reference, excerpt or topic assignment")
		}
		locator, license, licenseURL, normalization := "data.description, paragraph 1", "CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/", "HTML removed; whitespace normalized."
		suffix := "-description-01"
		if strings.HasSuffix(item.ChunkID, "-medium") {
			suffix = "-medium"
			locator, license, licenseURL, normalization = "data.medium_display", "CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/", "none"
		} else if item.ChunkID == "aic-11723-description-01" {
			normalization = "HTML removed; whitespace normalized; the source's split floral ligature normalized to floral."
		}
		if item.ChunkID != item.ArtworkID+suffix || item.SourceLocator != locator || item.License != license ||
			item.LicenseURL != licenseURL || item.Attribution != institution || item.Normalization != normalization {
			return nil, fmt.Errorf("chunk provenance violates the released catalog")
		}
		if _, exists := dataset.chunks[item.ChunkID]; exists {
			return nil, fmt.Errorf("duplicate chunk ID")
		}
		dataset.chunks[item.ChunkID] = item
	}
	return dataset, nil
}

func sameTopics(actual, expected []string) bool {
	if len(actual) != len(expected) {
		return false
	}
	seen := make(map[string]bool, len(actual))
	for _, topic := range actual {
		if seen[topic] {
			return false
		}
		seen[topic] = true
	}
	for _, topic := range expected {
		if !seen[topic] {
			return false
		}
	}
	return true
}

func validText(value string) bool {
	return strings.TrimSpace(value) != "" && !strings.ContainsFunc(value, func(r rune) bool {
		return (unicode.IsControl(r) && r != '\t' && r != '\n' && r != '\r') || (r >= 0xd800 && r <= 0xdfff)
	})
}

func objectFields(data []byte, fields string) (map[string]json.RawMessage, error) {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(data, &object); err != nil || object == nil {
		return nil, fmt.Errorf("expected an object")
	}
	names := strings.Fields(fields)
	if len(object) != len(names) {
		return nil, fmt.Errorf("missing or unknown object fields")
	}
	for _, name := range names {
		value, exists := object[name]
		if !exists || (bytes.Equal(value, []byte("null")) && name != "image_url") {
			return nil, fmt.Errorf("missing or null field %s", name)
		}
		if len(value) > 0 && value[0] == '"' {
			var text string
			if err := json.Unmarshal(value, &text); err != nil || !validText(text) {
				return nil, fmt.Errorf("field %s must contain valid nonempty text", name)
			}
		}
	}
	return object, nil
}
