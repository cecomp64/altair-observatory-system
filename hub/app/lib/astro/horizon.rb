module Astro
  # A telescope's horizon mask: minimum clear altitude by azimuth, linearly
  # interpolated, wrapping at 360 deg. The effective limit is the higher of
  # the mask and the minimum imaging altitude.
  class Horizon
    attr_reader :min_altitude

    def initialize(points = [], min_altitude: 0.0)
      @points = points.map { |az, alt| [ az.to_f % 360, alt.to_f ] }.sort_by(&:first)
      @min_altitude = min_altitude.to_f
    end

    def mask?
      @points.any?
    end

    def mask_altitude(azimuth)
      return 0.0 if @points.empty?
      return @points.first.last if @points.size == 1

      az = azimuth.to_f % 360
      upper_index = @points.index { |p_az, _| p_az >= az }
      lower = upper_index.nil? || upper_index.zero? ? @points.last : @points[upper_index - 1]
      upper = upper_index.nil? ? @points.first : @points[upper_index]
      span = (upper[0] - lower[0]) % 360
      return lower[1] if span.zero?

      fraction = ((az - lower[0]) % 360) / span
      lower[1] + (upper[1] - lower[1]) * fraction
    end

    def effective_min_altitude(azimuth)
      [ mask_altitude(azimuth), @min_altitude ].max
    end

    def clear?(altitude, azimuth)
      altitude >= effective_min_altitude(azimuth)
    end
  end
end
