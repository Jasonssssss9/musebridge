// Package config loads and validates the local Go API configuration.
package config

import (
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/Jasonssssss9/musebridge/api/internal/voice"
)

// Config describes the native development profile. Container deployments can
// override the host, seed path, public origin and private retrieval address.
type Config struct {
	Host              string
	Port              string
	PublicBaseURL     string
	CookieSecure      bool
	DatasetVersion    string
	SeedPath          string
	RetrievalBaseURL  string
	GenerationMode    string
	AgentBaseURL      string
	AgentServiceToken string
	Voice             voice.Config
}

// Formatting configuration must never reveal a private service credential.
func (c Config) String() string   { return "config.Config (credentials redacted)" }
func (c Config) GoString() string { return c.String() }

func (c Config) WriteTimeout() time.Duration {
	if c.GenerationMode == "agent" {
		return 65 * time.Second
	}
	return 15 * time.Second
}

// ReadTimeout allows a phone to upload a 3 MiB recording over a tunnel within the
// voice stage budget; other deployments keep the short default.
func (c Config) ReadTimeout() time.Duration {
	if c.Voice.Enabled {
		return 35 * time.Second
	}
	return 10 * time.Second
}

func (c Config) DrainTimeout() time.Duration {
	if c.GenerationMode == "agent" {
		return 65 * time.Second
	}
	return 10 * time.Second
}

// Load reads environment variables through lookup. Explicitly empty values are
// rejected rather than silently replaced by defaults.
func Load(lookup func(string) (string, bool)) (Config, error) {
	defaults := map[string]string{
		"HOST": "127.0.0.1", "PORT": "8000",
		"PUBLIC_BASE_URL":    "http://localhost:8080",
		"DATASET_VERSION":    "aic-seed-v0.1",
		"SEED_PATH":          "../knowledge/data/aic-seed-v0.1.json",
		"RETRIEVAL_BASE_URL": "http://127.0.0.1:8001",
		"GENERATION_MODE":    "fixture",
	}
	values := make(map[string]string, len(defaults))
	for key, fallback := range defaults {
		value, exists := lookup(key)
		if !exists {
			value = fallback
		}
		if strings.TrimSpace(value) == "" || strings.TrimSpace(value) != value || strings.ContainsAny(value, "\x00\r\n") {
			return Config{}, fmt.Errorf("%s must be nonempty and contain no surrounding whitespace or invalid characters", key)
		}
		values[key] = value
	}
	if strings.ContainsAny(values["HOST"], "/[]") || strings.ContainsFunc(values["HOST"], unicode.IsSpace) {
		return Config{}, fmt.Errorf("HOST must be a hostname or an unbracketed IP address")
	}
	if !decimalPort(values["PORT"]) {
		return Config{}, fmt.Errorf("PORT must be an integer from 1 to 65535")
	}
	publicURL, err := origin(values["PUBLIC_BASE_URL"])
	if err != nil {
		return Config{}, fmt.Errorf("PUBLIC_BASE_URL: %w", err)
	}
	if _, err := origin(values["RETRIEVAL_BASE_URL"]); err != nil {
		return Config{}, fmt.Errorf("RETRIEVAL_BASE_URL: %w", err)
	}
	secure := publicURL.Scheme == "https"
	if value, exists := lookup("COOKIE_SECURE"); exists {
		switch value {
		case "true":
			secure = true
		case "false":
			secure = false
		default:
			return Config{}, fmt.Errorf("COOKIE_SECURE must be true or false")
		}
	}
	if secure != (publicURL.Scheme == "https") {
		return Config{}, fmt.Errorf("COOKIE_SECURE must match the PUBLIC_BASE_URL HTTP or HTTPS profile")
	}
	if values["GENERATION_MODE"] != "fixture" && values["GENERATION_MODE"] != "agent" {
		return Config{}, fmt.Errorf("GENERATION_MODE must be fixture or agent")
	}
	cfg := Config{
		Host: values["HOST"], Port: values["PORT"],
		PublicBaseURL: browserOrigin(publicURL), CookieSecure: secure,
		DatasetVersion: values["DATASET_VERSION"], SeedPath: values["SEED_PATH"],
		RetrievalBaseURL: values["RETRIEVAL_BASE_URL"], GenerationMode: values["GENERATION_MODE"],
	}
	if cfg.GenerationMode == "agent" {
		cfg.AgentBaseURL = "http://127.0.0.1:8002"
		if value, exists := lookup("AGENT_BASE_URL"); exists {
			cfg.AgentBaseURL = value
		}
		if _, err := origin(cfg.AgentBaseURL); err != nil {
			return Config{}, fmt.Errorf("AGENT_BASE_URL: %w", err)
		}
		cfg.AgentServiceToken, _ = lookup("AGENT_SERVICE_TOKEN")
		if len(cfg.AgentServiceToken) < 43 || strings.ContainsFunc(cfg.AgentServiceToken, func(r rune) bool { return r < 33 || r > 126 }) {
			return Config{}, fmt.Errorf("AGENT_SERVICE_TOKEN must encode at least 32 random bytes as visible ASCII")
		}
	}
	cfg.Voice, err = voice.LoadConfig(lookup)
	if err != nil {
		return Config{}, err
	}
	if cfg.Voice.Enabled {
		if cfg.GenerationMode != "agent" {
			return Config{}, fmt.Errorf("VINCENT_ENABLED=true requires GENERATION_MODE=agent")
		}
		// Safari only grants the microphone on a trusted HTTPS page (or localhost).
		if publicURL.Scheme != "https" && publicURL.Hostname() != "localhost" && publicURL.Hostname() != "127.0.0.1" {
			return Config{}, fmt.Errorf("VINCENT_ENABLED=true requires an https PUBLIC_BASE_URL, such as the HTTPS tunnel origin")
		}
	}
	return cfg, nil
}

// Address is suitable for net.Listen, including when Host is an IPv6 address.
func (c Config) Address() string {
	return net.JoinHostPort(c.Host, c.Port)
}

func decimalPort(value string) bool {
	if value == "" || strings.Trim(value, "0123456789") != "" {
		return false
	}
	port, err := strconv.Atoi(value)
	return err == nil && port >= 1 && port <= 65535
}

func origin(value string) (*url.URL, error) {
	u, err := url.Parse(value)
	if err != nil || u == nil || (u.Scheme != "http" && u.Scheme != "https") ||
		u.Host == "" || u.Hostname() == "" || u.User != nil || u.Opaque != "" ||
		u.Path != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || strings.Contains(value, "#") ||
		strings.ContainsFunc(value, unicode.IsSpace) || strings.HasSuffix(u.Host, ":") {
		return nil, fmt.Errorf("use an HTTP(S) origin without credentials, path, query, fragment or trailing slash")
	}
	if u.Port() != "" && !decimalPort(u.Port()) {
		return nil, fmt.Errorf("the origin port must be an integer from 1 to 65535")
	}
	if strings.Contains(u.Hostname(), ":") && net.ParseIP(u.Hostname()) == nil {
		return nil, fmt.Errorf("invalid IPv6 host")
	}
	return u, nil
}

// browserOrigin serializes the validated origin the way the browser sends it.
// Public POST requests still require an exact match against that origin.
func browserOrigin(u *url.URL) string {
	host := strings.ToLower(u.Hostname())
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	defaultPort := 80
	if u.Scheme == "https" {
		defaultPort = 443
	}
	port, _ := strconv.Atoi(u.Port())
	if port != 0 && port != defaultPort {
		host += ":" + strconv.Itoa(port)
	}
	return u.Scheme + "://" + host
}
