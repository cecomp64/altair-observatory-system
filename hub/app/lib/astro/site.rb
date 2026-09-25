module Astro
  # Where visibility is computed: a telescope's location, timezone and horizon.
  Site = Struct.new(:latitude, :longitude, :elevation_m, :timezone, :horizon_points, :min_altitude, keyword_init: true) do
    def self.for(telescope, min_altitude: nil)
      new(
        latitude: telescope.latitude.to_f, longitude: telescope.longitude.to_f, elevation_m: telescope.elevation_m.to_f,
        timezone: telescope.timezone, horizon_points: telescope.horizon_points,
        min_altitude: (min_altitude || telescope.min_altitude_deg).to_f
      )
    end

    def time_zone
      ActiveSupport::TimeZone[timezone] || ActiveSupport::TimeZone["UTC"]
    end

    def horizon(min_altitude: self.min_altitude)
      Horizon.new(horizon_points || [], min_altitude: min_altitude)
    end

    # Stable identity for caching (location + horizon + limit).
    def cache_key
      Digest::SHA256.hexdigest([ latitude, longitude, timezone, horizon_points, min_altitude ].to_json)[0, 16]
    end
  end
end
