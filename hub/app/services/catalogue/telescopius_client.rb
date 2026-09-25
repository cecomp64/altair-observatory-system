module Catalogue
  # Minimal Telescopius API client (port of telescopius.py): looks an object up
  # by name. The key comes from TELESCOPIUS_API_KEY or Rails credentials.
  class TelescopiusClient
    BASE_URL = ENV.fetch("TELESCOPIUS_API_URL", "https://api.telescopius.com/v2.0")
    TYPE_NAMES = {
      "gxy" => "Galaxy", "sgx" => "Spiral Galaxy", "eneb" => "Emission Nebula", "rneb" => "Reflection Nebula",
      "pneb" => "Planetary Nebula", "snr" => "Supernova Remnant", "ocl" => "Open Cluster",
      "gcl" => "Globular Cluster", "dneb" => "Dark Nebula"
    }.freeze

    Result = Struct.new(:name, :ra_deg, :dec_deg, :object_type, :magnitude, :constellation, :aliases, keyword_init: true)

    def self.api_key
      ENV["TELESCOPIUS_API_KEY"].presence || Rails.application.credentials.dig(:telescopius, :api_key)
    end

    def self.configured?
      api_key.present?
    end

    def initialize(api_key: self.class.api_key, connection: nil)
      @api_key = api_key
      @connection = connection || Faraday.new(url: BASE_URL, request: { timeout: 15 }) do |f|
        f.headers["Authorization"] = "Key #{@api_key}"
        f.headers["Accept"] = "application/json"
      end
    end

    # Returns a Result or nil. Raises on transport errors so callers can
    # tell "not found" from "couldn't ask".
    def search(name)
      response = @connection.get("targets/search", { name: name, lat: 0, lon: 0, timezone: "UTC", results_per_page: 1 })
      raise "Telescopius HTTP #{response.status}" unless response.success?

      object = JSON.parse(response.body).dig("page_results", 0, "object")
      object && parse(object)
    end

    private

    def parse(data)
      aliases = []
      Array(data["ids"]).each { |id| aliases << id unless id == data["main_id"] }
      aliases.concat(Array(data["alt_ids"]))
      Array(data["names"]).each { |n| aliases << n unless n == data["main_name"] }
      types = Array(data["types"])
      object_type = types.lazy.map { |t| TYPE_NAMES[t.to_s.downcase] }.find(&:itself) || types.first

      Result.new(
        name: data["main_name"].presence || data["main_id"],
        ra_deg: ra_degrees(data["ra"]), dec_deg: data["dec"]&.to_f,
        object_type: object_type, magnitude: data["visual_mag"] || data["photo_mag"],
        constellation: data["con_name"] || data["con"], aliases: ([ data["main_id"] ] + aliases).compact.uniq
      )
    end

    # Telescopius returns RA in hours.
    def ra_degrees(ra)
      return nil if ra.nil?

      (ra.to_f * 15.0) % 360
    end
  end
end
