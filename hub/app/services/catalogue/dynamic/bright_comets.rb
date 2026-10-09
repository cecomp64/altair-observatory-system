module Catalogue
  module Dynamic
    # Comets currently brighter than a limiting magnitude.
    #
    # - Which comets and how bright: the Comet Observation Database (COBS,
    #   https://www.cobs.si/api/), comet_list.api. current_mag is COBS's
    #   estimate from observers' reports, so it is a better "bright now" test
    #   than the predicted magnitudes in orbit databases. Active comets at or
    #   under MAX_MAGNITUDE (env COMET_MAX_MAGNITUDE, default 12) are listed.
    # - Where they are: JPL's Small-Body Observability API
    #   (https://ssd-api.jpl.nasa.gov/doc/sbwobs.html) for every comet, once,
    #   with optical=false and a window of just under 24 h so no comet is
    #   dropped for being below the horizon. Comets are matched by
    #   designation ("10P", "2026 R2"). RA/Dec are apparent positions at some
    #   time in that window: good to well under a degree, and comets are
    #   re-positioned on every refresh. A comet JPL has no position for is
    #   skipped (counted as "skipped").
    #
    # Neither API needs a key. A refresh is two requests.
    class BrightComets
      KEY = "bright_comets".freeze
      NAME = "Bright comets".freeze
      SHORT_NAME = "Comet".freeze
      DESCRIPTION = "Comets that observers currently report brighter than magnitude #{ENV.fetch('COMET_MAX_MAGNITUDE', 12)} (COBS), with JPL positions.".freeze
      SOURCE = "cobs".freeze
      SETUP = "nothing required. COMET_MAX_MAGNITUDE sets the faintest magnitude listed (default 12).".freeze
      COBS_URL = ENV.fetch("COBS_API_URL", "https://www.cobs.si/api").freeze
      JPL_URL = ENV.fetch("JPL_SSD_API_URL", "https://ssd-api.jpl.nasa.gov").freeze
      COMET_URL = "https://www.cobs.si/comet/%d".freeze
      MAX_MAGNITUDE = Float(ENV.fetch("COMET_MAX_MAGNITUDE", 12))
      # A comet's position and magnitude change daily: keep them current on
      # the catalogue object, not just when it is first created.
      LIVE_ATTRIBUTES = %i[ra_deg dec_deg magnitude].freeze

      def self.configured? = true

      attr_reader :skipped

      def initialize(max_magnitude: MAX_MAGNITUDE, cobs: nil, jpl: nil, now: Time.current)
        @max_magnitude = max_magnitude
        @now = now.utc
        @skipped = []
        @cobs = cobs || connection(COBS_URL, 60)
        @jpl = jpl || connection(JPL_URL, 120)
      end

      def fetch
        comets = bright_comets
        return [] if comets.empty?

        positions = positions_by_designation
        comets.filter_map do |comet|
          position = positions[jpl_designation(comet)]
          if position.nil?
            @skipped << comet["fullname"]
            next
          end
          record(comet, position)
        end
      end

      def report
        { "skipped" => skipped.size }.select { |_, n| n.positive? }
      end

      private

      def bright_comets
        data = get_json(@cobs, "comet_list.api", {}, "COBS")
        raise "COBS comet list has no objects list" unless data["objects"].is_a?(Array)

        data["objects"].select do |comet|
          mag = number(comet["current_mag"])
          comet["is_active"] && mag && mag <= @max_magnitude && comet["name"].present?
        end.sort_by { |comet| number(comet["current_mag"]) }
      end

      # { designation => { ra:, dec:, full_name:, vmag: } } for every comet
      # JPL has a position for.
      def positions_by_designation
        start = @now.change(min: 0, sec: 0)
        data = get_json(@jpl, "sbwobs.api", {
          "sb-kind" => "c", "lat" => 0, "lon" => 0, "alt" => 0, "optical" => false,
          "elong-min" => 0, "elong-max" => 180, "elev-min" => 0, "fmt-ra-dec" => false,
          "obs-time" => start.strftime("%Y-%m-%dT%H:%M:%S"), "obs-end" => (start + 23.hours + 55.minutes).strftime("%Y-%m-%dT%H:%M:%S")
        }, "JPL")
        fields = Array(data["fields"])
        raise "JPL sbwobs returned no fields list" if fields.empty? || !data["data"].is_a?(Array)

        col = ->(name) { fields.index(name) or raise "JPL sbwobs has no #{name.inspect} column" }
        designation, full_name, ra, dec, vmag = col["Designation"], col["Full name"], col["R.A."], col["Dec."], col["Vmag"]
        data["data"].each_with_object({}) do |row, found|
          next unless number(row[ra]) && number(row[dec])

          found[row[designation].to_s.strip] = { ra: number(row[ra]), dec: number(row[dec]), full_name: row[full_name].to_s.squish, vmag: number(row[vmag]) }
        end
      end

      # COBS names new-style comets "P/2026 R2", JPL designates them "2026 R2";
      # numbered ones are "10P" in both.
      def jpl_designation(comet)
        comet["name"].to_s.strip.sub(%r{\A[A-Z]/}, "")
      end

      def record(comet, position)
        aliases = [ [ comet["name"].to_s.strip, nil ], [ position[:full_name], nil ] ].reject { |name, _| name.blank? || name == comet["fullname"] }
        Record.new(
          primary_name: comet["fullname"].presence || comet["name"], aliases: aliases.uniq,
          attributes: {
            ra_deg: position[:ra] % 360, dec_deg: position[:dec].clamp(-90, 90), magnitude: number(comet["current_mag"]),
            object_type: "Comet", source_ref: comet["name"].to_s.strip
          },
          details: {
            "designation" => comet["name"].to_s.strip, "current_mag" => number(comet["current_mag"]),
            "peak_mag" => number(comet["peak_mag"]), "peak_mag_date" => comet["peak_mag_date"].presence,
            "perihelion_mag" => number(comet["perihelion_mag"]), "perihelion_date" => comet["perihelion_date"].presence,
            "observed" => comet["is_observed"] ? true : nil, "cobs_id" => comet["id"],
            "ra_deg" => position[:ra], "dec_deg" => position[:dec], "position_at" => @now.iso8601
          }.compact
        )
      end

      def connection(url, timeout)
        Faraday.new(url: url, request: { timeout: timeout }) do |f|
          f.headers["Accept"] = "application/json"
          f.headers["User-Agent"] = "AltairObservatoryHub (dynamic catalogues)"
        end
      end

      # COBS answers some errors with HTTP 200 and a "code" in the body.
      def get_json(connection, path, params, name)
        response = connection.get(path, params)
        raise "#{name} #{path} HTTP #{response.status}: #{response.body.to_s.truncate(200)}" unless response.success?

        data = JSON.parse(response.body)
        raise "#{name} #{path}: #{data['message']}" if data.is_a?(Hash) && data["code"].to_s.match?(/\A[45]\d\d\z/)

        data
      end

      def number(value)
        Float(value.to_s.strip)
      rescue ArgumentError, TypeError
        nil
      end
    end
  end
end
