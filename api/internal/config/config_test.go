package config

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

func environment(values map[string]string) func(string) (string, bool) {
	return func(key string) (string, bool) {
		value, ok := values[key]
		return value, ok
	}
}

func TestAgentConfigurationAndBudgets(t *testing.T) {
	token := strings.Repeat("x", 43)
	cfg, err := Load(environment(map[string]string{"GENERATION_MODE": "agent", "AGENT_SERVICE_TOKEN": token}))
	if err != nil || cfg.AgentBaseURL != "http://127.0.0.1:8002" || cfg.AgentServiceToken != token ||
		cfg.WriteTimeout() != 65*time.Second || cfg.DrainTimeout() != 65*time.Second {
		t.Fatal("Agent integration configuration or deadline hierarchy is invalid", err)
	}
	if strings.Contains(fmt.Sprintf("%v %+v %#v", cfg, cfg, cfg), token) {
		t.Fatal("configuration formatting revealed the service credential")
	}
	fixture, err := Load(environment(nil))
	if err != nil || fixture.WriteTimeout() != 15*time.Second || fixture.DrainTimeout() != 10*time.Second || fixture.AgentServiceToken != "" {
		t.Fatal("fixture configuration changed unexpectedly")
	}
	for _, tc := range []struct{ key, value string }{
		{"AGENT_SERVICE_TOKEN", ""}, {"AGENT_SERVICE_TOKEN", strings.Repeat("x", 42)},
		{"AGENT_SERVICE_TOKEN", token + " "}, {"AGENT_SERVICE_TOKEN", token + "\n"}, {"AGENT_SERVICE_TOKEN", token + "中"},
		{"AGENT_BASE_URL", ""}, {"AGENT_BASE_URL", "http://agent:8002/"},
		{"AGENT_BASE_URL", "http://agent/path"}, {"AGENT_BASE_URL", "http://user:PRIVATE@agent"},
		{"AGENT_BASE_URL", "http://agent:99999"}, {"AGENT_BASE_URL", "http://agent?"},
	} {
		t.Run(tc.key+"-invalid", func(t *testing.T) {
			_, err := Load(environment(map[string]string{"GENERATION_MODE": "agent", "AGENT_SERVICE_TOKEN": token, tc.key: tc.value}))
			if err == nil || !strings.Contains(err.Error(), tc.key) || strings.Contains(err.Error(), "PRIVATE") || strings.Contains(err.Error(), token) {
				t.Fatal("invalid Agent configuration did not produce a safe actionable error", err)
			}
		})
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

func TestPublicBaseURLUsesBrowserOrigin(t *testing.T) {
	cases := []struct{ configured, expected string }{
		{"http://LOCALHOST:80", "http://localhost"},
		{"https://Museum.Example:443", "https://museum.example"},
		{"http://Museum.Example:08080", "http://museum.example:8080"},
		{"https://Museum.Example:80", "https://museum.example:80"},
		{"http://[::1]:80", "http://[::1]"},
		{"https://[2001:DB8::1]:8443", "https://[2001:db8::1]:8443"},
	}
	for _, tc := range cases {
		t.Run(tc.configured, func(t *testing.T) {
			cfg, err := Load(environment(map[string]string{"PUBLIC_BASE_URL": tc.configured}))
			if err != nil {
				t.Fatal(err)
			}
			if cfg.PublicBaseURL != tc.expected {
				t.Fatalf("public origin = %q, want %q", cfg.PublicBaseURL, tc.expected)
			}
		})
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
