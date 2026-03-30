FROM golang:1.22-alpine AS builder

WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download
COPY main.go .
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o anysong .

FROM alpine:3.20

RUN apk add --no-cache ffmpeg python3 py3-pip curl && \
    pip3 install --break-system-packages yt-dlp

COPY --from=builder /build/anysong /usr/local/bin/anysong

RUN mkdir -p /music

ENTRYPOINT ["anysong"]
CMD ["--help"]
