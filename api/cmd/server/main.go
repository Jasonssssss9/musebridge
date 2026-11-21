// Command server starts the local MuseBridge application API.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/config"
	"github.com/Jasonssssss9/musebridge/api/internal/httpapi"
	"github.com/Jasonssssss9/musebridge/api/internal/retrieval"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stderr, nil))
	if err := run(logger); err != nil {
		logger.Error("startup_failed", "service", "api", "error", err.Error())
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	cfg, err := config.Load(os.LookupEnv)
	if err != nil {
		return err
	}
	dataset, err := catalog.Load(cfg.SeedPath, cfg.DatasetVersion)
	if err != nil {
		return err
	}
	settings := httpapi.Settings{PublicBaseURL: cfg.PublicBaseURL, CookieSecure: cfg.CookieSecure}
	var public http.Handler
	if cfg.GenerationMode == "agent" {
		client, err := agentclient.New(cfg.AgentBaseURL, cfg.AgentServiceToken)
		if err != nil {
			return err
		}
		defer client.CloseIdleConnections()
		public, err = httpapi.NewAgent(dataset, logger, settings, client)
		if err != nil {
			return err
		}
	} else {
		retriever, err := retrieval.New(cfg.RetrievalBaseURL, dataset)
		if err != nil {
			return err
		}
		defer retriever.CloseIdleConnections()
		public = httpapi.New(dataset, logger, settings, retriever)
	}
	listener, err := net.Listen("tcp", cfg.Address())
	if err != nil {
		return fmt.Errorf("listen: %w", err)
	}
	server := &http.Server{
		Handler:           public,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second, WriteTimeout: cfg.WriteTimeout(),
		IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10,
		ErrorLog: slog.NewLogLogger(logger.Handler(), slog.LevelError),
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	serveErr := make(chan error, 1)
	go func() { serveErr <- server.Serve(listener) }()
	logger.Info("service_started", "service", "api", "address", listener.Addr().String(),
		"dataset_version", dataset.Version(), "seed_sha256", dataset.Checksum(), "generation_mode", cfg.GenerationMode)
	select {
	case err := <-serveErr:
		if !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		return nil
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.DrainTimeout())
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			_ = server.Close()
			return fmt.Errorf("shutdown: %w", err)
		}
		if err := <-serveErr; !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		logger.Info("service_stopped", "service", "api")
		return nil
	}
}
