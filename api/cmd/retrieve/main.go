// Command retrieve exercises B's real client without a conversation or web UI.
package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/config"
	"github.com/Jasonssssss9/musebridge/api/internal/retrieval"
)

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(arguments []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("retrieve", flag.ContinueOnError)
	flags.SetOutput(stderr)
	artworkID := flags.String("artwork-id", "", "Canonical artwork ID.")
	query := flags.String("query", "", "Prepared retrieval query.")
	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if flags.NArg() != 0 || *artworkID == "" || *query == "" {
		fmt.Fprintln(stderr, "Provide --artwork-id and --query, with no positional arguments.")
		return 2
	}
	requestID := "req-" + rand.Text()
	fail := func(code, message string, retryable bool) int {
		_ = json.NewEncoder(stderr).Encode(struct {
			RequestID string           `json:"request_id"`
			Error     *retrieval.Error `json:"error"`
		}{requestID, &retrieval.Error{Code: code, Message: message, Retryable: retryable}})
		return 2
	}
	cfg, err := config.Load(os.LookupEnv)
	if err != nil {
		return fail("invalid_configuration", err.Error(), false)
	}
	dataset, err := catalog.Load(cfg.SeedPath, cfg.DatasetVersion)
	if err != nil {
		return fail("invalid_dataset", err.Error(), false)
	}
	client, err := retrieval.New(cfg.RetrievalBaseURL, dataset)
	if err != nil {
		return fail("invalid_configuration", err.Error(), false)
	}
	defer client.CloseIdleConnections()
	result, err := client.Retrieve(context.Background(), retrieval.Request{
		ArtworkID: *artworkID, Query: *query, RequestID: requestID,
	})
	if err != nil {
		var failure *retrieval.Error
		if errors.As(err, &failure) {
			return fail(failure.Code, failure.Message, failure.Retryable)
		}
		return fail("internal_error", "The retrieval attempt could not be completed.", true)
	}
	encoder := json.NewEncoder(stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		return fail("output_error", "The retrieval result could not be written.", false)
	}
	return 0
}
