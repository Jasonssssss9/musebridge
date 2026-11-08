package catalog

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const releasedSeed = "../../../knowledge/data/aic-seed-v0.1.json"

func seedBytes(t *testing.T) []byte {
	t.Helper()
	data, err := os.ReadFile(releasedSeed)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func writeSeed(t *testing.T, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "seed.json")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestReleasedCatalog(t *testing.T) {
	dataset, err := Load(releasedSeed, "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	if dataset.Version() != "aic-seed-v0.1" || dataset.Checksum() != "3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac" {
		t.Fatal("release identity did not match the published artifact")
	}
	for slug, title := range map[string]string{"aic-28560": "The Bedroom", "aic-11723": "Woman at Her Toilette"} {
		artwork, found := dataset.ArtworkBySlug(slug)
		if !found || artwork.Title != title || artwork.ImageURL != nil {
			t.Fatalf("incorrect artwork for %s: %+v", slug, artwork)
		}
		artwork.Title = "Changed response"
		again, _ := dataset.ArtworkBySlug(slug)
		if again.Title != title {
			t.Fatal("a returned artwork could mutate the loaded catalog")
		}
	}
	if _, found := dataset.ArtworkBySlug("missing"); found {
		t.Fatal("unknown artwork was found")
	}
}

func TestSeedRelationshipsAndRequiredFields(t *testing.T) {
	first := func(root map[string]any, collection string) map[string]any {
		return root[collection].([]any)[0].(map[string]any)
	}
	cases := map[string]func(map[string]any){
		"wrong release":         func(root map[string]any) { root["dataset_version"] = "wrong" },
		"null collection":       func(root map[string]any) { root["sources"] = nil },
		"missing collection":    func(root map[string]any) { delete(root, "chunks") },
		"empty collection":      func(root map[string]any) { root["artworks"] = []any{} },
		"unknown root field":    func(root map[string]any) { root["debug"] = true },
		"unknown artwork field": func(root map[string]any) { first(root, "artworks")["debug"] = true },
		"wrong field case": func(root map[string]any) {
			artwork := first(root, "artworks")
			artwork["Title"] = artwork["title"]
			delete(artwork, "title")
		},
		"missing image field":      func(root map[string]any) { delete(first(root, "artworks"), "image_url") },
		"null required field":      func(root map[string]any) { first(root, "artworks")["title"] = nil },
		"blank title":              func(root map[string]any) { first(root, "artworks")["title"] = " " },
		"wrong title type":         func(root map[string]any) { first(root, "artworks")["title"] = true },
		"control character":        func(root map[string]any) { first(root, "artworks")["title"] = "bad\x00title" },
		"duplicate artwork":        func(root map[string]any) { root["artworks"].([]any)[1] = first(root, "artworks") },
		"artwork version":          func(root map[string]any) { first(root, "artworks")["dataset_version"] = "other" },
		"record type":              func(root map[string]any) { first(root, "artworks")["museum_record_id"] = true },
		"unreviewed image":         func(root map[string]any) { first(root, "artworks")["image_url"] = "https://example.com/image" },
		"wrong source URL":         func(root map[string]any) { first(root, "sources")["source_url"] = "https://example.com" },
		"invalid timestamp":        func(root map[string]any) { first(root, "sources")["retrieved_at"] = "yesterday" },
		"short timestamp hour":     func(root map[string]any) { first(root, "sources")["retrieved_at"] = "2026-11-08T2:53:29Z" },
		"comma timestamp fraction": func(root map[string]any) { first(root, "sources")["retrieved_at"] = "2026-11-08T02:53:29,5Z" },
		"non UTC timestamp":        func(root map[string]any) { first(root, "sources")["retrieved_at"] = "2026-11-08T02:53:29-08:00" },
		"duplicate source":         func(root map[string]any) { root["sources"].([]any)[1] = first(root, "sources") },
		"cross artwork source":     func(root map[string]any) { first(root, "chunks")["source_id"] = "aic-api-11723" },
		"unknown chunk":            func(root map[string]any) { first(root, "chunks")["chunk_id"] = "invented" },
		"wrong locator":            func(root map[string]any) { first(root, "chunks")["source_locator"] = "page 99" },
		"wrong license":            func(root map[string]any) { first(root, "chunks")["license"] = "CC-BY-4.0" },
		"wrong attribution":        func(root map[string]any) { first(root, "chunks")["attribution"] = "someone else" },
		"duplicate chunk":          func(root map[string]any) { root["chunks"].([]any)[1] = first(root, "chunks") },
		"long excerpt":             func(root map[string]any) { first(root, "chunks")["excerpt"] = strings.Repeat("x", 4001) },
		"duplicate topics":         func(root map[string]any) { first(root, "chunks")["topics"] = []any{"material", "material"} },
		"wrong topics":             func(root map[string]any) { first(root, "chunks")["topics"] = []any{"brushwork"} },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			var root map[string]any
			if err := json.Unmarshal(seedBytes(t), &root); err != nil {
				t.Fatal(err)
			}
			mutate(root)
			data, err := json.Marshal(root)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := Load(writeSeed(t, data), "aic-seed-v0.1"); err == nil {
				t.Fatal("invalid seed was accepted")
			}
		})
	}
}

func TestJSONIntegrity(t *testing.T) {
	valid := string(seedBytes(t))
	cases := map[string]string{
		"malformed": "{", "null": "null", "array": "[]", "BOM": "\ufeff" + valid,
		"trailing content": valid + " {}",
		"invalid UTF-8":    strings.Replace(valid, "Oil on canvas", "Oil\xff", 1),
		"duplicate root":   strings.Replace(valid, `"dataset_version":`, `"dataset_version":"other","dataset_version":`, 1),
		"duplicate nested": strings.Replace(valid, `"title":`, `"title":"other","title":`, 1),
		"high surrogate":   strings.Replace(valid, "Oil on canvas", `\ud800`, 1),
		"low surrogate":    strings.Replace(valid, "Oil on canvas", `\udc00`, 1),
	}
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := Load(writeSeed(t, []byte(data)), "aic-seed-v0.1"); err == nil {
				t.Fatal("invalid JSON was accepted")
			}
		})
	}
	paired := strings.Replace(valid, "Oil on canvas", `Oil on canvas \ud83c\udfa8`, 1)
	if _, err := Load(writeSeed(t, []byte(paired)), "aic-seed-v0.1"); err != nil {
		t.Fatalf("valid surrogate pair was rejected: %v", err)
	}
	if _, err := Load(filepath.Join(t.TempDir(), "missing.json"), "aic-seed-v0.1"); err == nil {
		t.Fatal("missing seed silently fell back")
	}
}
