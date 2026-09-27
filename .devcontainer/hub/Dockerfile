# Dev image for the Hub. Ruby matches hub/.ruby-version and the production Dockerfile.
ARG RUBY_VERSION=3.3.6
FROM docker.io/library/ruby:$RUBY_VERSION-bookworm

# libpq and libvips for pg and image_processing, Chromium for the Cuprite system specs.
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y \
      build-essential git libpq-dev postgresql-client libvips libyaml-dev pkg-config \
      chromium fonts-liberation && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

ENV BROWSER_PATH=/usr/bin/chromium
