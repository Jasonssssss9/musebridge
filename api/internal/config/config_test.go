package config

import (
	"strings"
	"testing"
)

func environment(values map[string]string) func(string) (string, bool) {
	return func(key string) (string, bool) {
		value, ok := values[key]
		return value, ok
	}
}

func TestDevelopmentDefaults(t *testing.T) {
	cfg, err := Load(environment(nil))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Address() != "127.0.0.1:8000" || cfg.CookieSecure || cfg.DatasetVersion != "aic-seed-v0.1" ||
		cfg.GenerationMode != "fixture" || cfg.SeedPath != "../knowledge/data/aic-seed-v0.1.json" {
		t.Fatalf("unexpected development configuration: %+v", cfg)
	}
}

func TestHTTPSAndContainerOverrides(t *testing.T) {
	cfg, err := Load(environment(map[string]string{
		"HOST": "::", "PORT": "9000", "PUBLIC_BASE_URL": "https://museum.example",
		"RETRIEVAL_BASE_URL": "http://retrieval:8001", "SEED_PATH": "/data/seed.json",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.CookieSecure || cfg.Address() != "[::]:9000" || cfg.SeedPath != "/data/seed.json" {
		t.Fatalf("overrides were not applied: %+v", cfg)
	}
}

func TestInvalidConfiguration(t *testing.T) {
	cases := []struct{ key, value string }{
		{"HOST", ""}, {"HOST", "bad host"}, {"HOST", "[::1]"},
		{"PORT", "0"}, {"PORT", "65536"}, {"PORT", "+8000"}, {"PORT", "8.0"},
		{"PUBLIC_BASE_URL", ""}, {"PUBLIC_BASE_URL", "http://localhost:8080/"},
		{"PUBLIC_BASE_URL", "ftp://museum.example"}, {"PUBLIC_BASE_URL", "http://user:password@localhost"},
		{"PUBLIC_BASE_URL", "http://localhost?"}, {"PUBLIC_BASE_URL", "http://localhost#"},
		{"PUBLIC_BASE_URL", "http://localhost:99999"}, {"PUBLIC_BASE_URL", "http://localhost:"},
		{"PUBLIC_BASE_URL", "http://localhost/path"}, {"PUBLIC_BASE_URL", "http://[invalid]"},
		{"RETRIEVAL_BASE_URL", "http://retrieval:8001/retrieve"},
		{"COOKIE_SECURE", "yes"}, {"COOKIE_SECURE", ""}, {"COOKIE_SECURE", "true"},
		{"DATASET_VERSION", " "}, {"SEED_PATH", ""}, {"GENERATION_MODE", "llm"},
	}
	for _, tc := range cases {
		t.Run(tc.key+"="+tc.value, func(t *testing.T) {
			if _, err := Load(environment(map[string]string{tc.key: tc.value})); err == nil || !strings.Contains(err.Error(), tc.key) {
				t.Fatalf("expected an actionable %s error, got %v", tc.key, err)
			}
		})
	}
	if _, err := Load(environment(map[string]string{
		"PUBLIC_BASE_URL": "https://museum.example", "COOKIE_SECURE": "false",
	})); err == nil {
		t.Fatal("HTTPS accepted insecure cookie configuration")
	}
}
