FROM golang:1.27-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/server ./cmd/server
COPY internal ./internal
RUN CGO_ENABLED=0 go build -mod=readonly -trimpath -o /out/musebridge-api ./cmd/server

FROM alpine:3.22

ENV HOST=0.0.0.0 PORT=8000
COPY --from=build /out/musebridge-api /usr/local/bin/musebridge-api

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -T 2 -O /dev/null "http://127.0.0.1:${PORT}/readyz" || exit 1

ENTRYPOINT ["/usr/local/bin/musebridge-api"]
