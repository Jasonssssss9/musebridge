// Package jsonwire checks JSON integrity before typed protocol decoding.
package jsonwire

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Validate rejects duplicate properties, trailing content, invalid UTF-8 and
// unpaired Unicode surrogates, which encoding/json otherwise tolerates.
func Validate(data []byte) error {
	if !utf8.Valid(data) || !json.Valid(data) {
		return fmt.Errorf("expected one valid UTF-8 JSON value")
	}
	inside := false
	for i := 0; i < len(data); i++ {
		if data[i] == '"' {
			inside = !inside
		}
		if !inside || data[i] != '\\' {
			continue
		}
		if data[i+1] != 'u' {
			i++
			continue
		}
		value, _ := strconv.ParseUint(string(data[i+2:i+6]), 16, 16)
		if value >= 0xd800 && value <= 0xdbff {
			if i+12 > len(data) || string(data[i+6:i+8]) != `\u` {
				return fmt.Errorf("unpaired Unicode surrogate")
			}
			low, err := strconv.ParseUint(string(data[i+8:i+12]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return fmt.Errorf("unpaired Unicode surrogate")
			}
			i += 11
		} else {
			if value >= 0xdc00 && value <= 0xdfff {
				return fmt.Errorf("unpaired Unicode surrogate")
			}
			i += 5
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var visit func() error
	visit = func() error {
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		switch token {
		case json.Delim('{'):
			seen := make(map[string]bool)
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return err
				}
				name := key.(string)
				if seen[name] {
					return fmt.Errorf("duplicate JSON property")
				}
				seen[name] = true
				if err := visit(); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
			return err
		case json.Delim('['):
			for decoder.More() {
				if err := visit(); err != nil {
					return err
				}
			}
			_, err = decoder.Token()
			return err
		}
		return nil
	}
	if err := visit(); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return fmt.Errorf("trailing JSON content")
	}
	return nil
}

// DecodeFields selects exact, required non-null field names, ignoring additive
// response metadata. Call Validate on the full document first. Selecting fields
// also prevents encoding/json's case-insensitive aliases from shadowing them.
func DecodeFields(data []byte, fields string, destination any) error {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(data, &object); err != nil || object == nil {
		return fmt.Errorf("expected a JSON object")
	}
	selected := make(map[string]json.RawMessage)
	for _, name := range strings.Fields(fields) {
		value, exists := object[name]
		if !exists || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return fmt.Errorf("missing or null field %s", name)
		}
		selected[name] = value
	}
	encoded, err := json.Marshal(selected)
	if err != nil {
		return err
	}
	return json.Unmarshal(encoded, destination)
}
