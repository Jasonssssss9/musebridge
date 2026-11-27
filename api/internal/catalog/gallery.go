package catalog

import (
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

const getty = "J. Paul Getty Museum"

var gettySlug = regexp.MustCompile(`^getty-[a-z0-9]+$`)

func (d *Dataset) loadGallery(seed seedFile) error {
	if len(seed.Artworks) == 0 {
		return fmt.Errorf("gallery must include artworks")
	}
	for _, art := range seed.Artworks {
		code := strings.TrimPrefix(art.Slug, "getty-")
		canonical := "https://www.getty.edu/art/collection/object/" + strings.ToUpper(code)
		imageSource, err := url.Parse(art.ImageSourceURL)
		if !gettySlug.MatchString(art.Slug) || art.ArtworkID != art.Slug || art.DatasetVersion != d.version ||
			art.Museum != getty || art.MuseumRecordID < 1 || strings.TrimSuffix(art.SourceURL, "/") != canonical ||
			art.ImageURL == nil || *art.ImageURL != "/artworks/"+art.Slug+".jpg" || art.ImageLicense != "CC0-1.0" ||
			err != nil || imageSource.Scheme != "https" || imageSource.Host != "media.getty.edu" ||
			imageSource.User != nil || !strings.HasPrefix(imageSource.Path, "/iiif/image/") ||
			len(art.SuggestedQuestions) == 0 || !uniqueText(art.SuggestedQuestions) {
			return fmt.Errorf("invalid gallery artwork %q", art.ArtworkID)
		}
		if _, exists := d.artworks[art.Slug]; exists {
			return fmt.Errorf("duplicate artwork %q", art.Slug)
		}
		d.artworks[art.Slug] = art
	}
	for _, src := range seed.Sources {
		art, ok := d.artworks[src.ArtworkID]
		_, err := time.Parse(time.RFC3339Nano, src.RetrievedAt)
		if !ok || src.SourceURL != art.SourceURL || src.SourceLanguage != "en" ||
			err != nil || !utcTimestamp.MatchString(src.RetrievedAt) {
			return fmt.Errorf("invalid gallery source %q", src.SourceID)
		}
		switch src.SourceKind {
		case "museum_record":
			if src.Institution != getty || src.SourceID != art.Slug+"-record" || src.BasedOnSourceID != "" {
				return fmt.Errorf("invalid museum record %q", src.SourceID)
			}
		case "guide_note":
			if src.Institution != "MuseBridge" || !strings.HasPrefix(src.SourceID, "musebridge-") || src.BasedOnSourceID != art.Slug+"-record" {
				return fmt.Errorf("invalid guide note %q", src.SourceID)
			}
		default:
			return fmt.Errorf("unknown source kind %q", src.SourceKind)
		}
		if _, exists := d.sources[src.SourceID]; exists {
			return fmt.Errorf("duplicate source %q", src.SourceID)
		}
		d.sources[src.SourceID] = src
	}
	for _, art := range seed.Artworks {
		if _, ok := d.ArtworkOrigin(art.ArtworkID); !ok {
			return fmt.Errorf("missing catalog origin for %q", art.ArtworkID)
		}
	}
	licenses := map[string]string{
		"CC0-1.0":      "https://creativecommons.org/publicdomain/zero/1.0/",
		"CC-BY-4.0":    "https://creativecommons.org/licenses/by/4.0/",
		"In Copyright": "https://rightsstatements.org/vocab/InC/1.0/",
	}
	hasChunk := make(map[string]bool)
	for _, item := range seed.Chunks {
		src, ok := d.sources[item.SourceID]
		if !ok || src.ArtworkID != item.ArtworkID || item.Attribution != src.Institution ||
			licenses[item.License] != item.LicenseURL || (src.SourceKind == "guide_note") != (item.License == "In Copyright") ||
			len(item.Topics) == 0 || !uniqueText(item.Topics) || utf8.RuneCountInString(item.Excerpt) > 4000 {
			return fmt.Errorf("invalid gallery chunk %q", item.ChunkID)
		}
		if _, exists := d.chunks[item.ChunkID]; exists {
			return fmt.Errorf("duplicate chunk %q", item.ChunkID)
		}
		d.chunks[item.ChunkID] = item
		hasChunk[item.SourceID] = true
	}
	for id := range d.sources {
		if !hasChunk[id] {
			return fmt.Errorf("no passages for source %q", id)
		}
	}
	return nil
}

func uniqueText(values []string) bool {
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		if !validText(value) || seen[value] {
			return false
		}
		seen[value] = true
	}
	return true
}
