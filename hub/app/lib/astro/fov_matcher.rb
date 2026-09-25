module Astro
  # Catalogue objects inside a frame's footprint (port of astrophotography-
  # database fov_matcher.py). Upgrade: the footprint is the rotated sensor
  # rectangle on the tangent plane, not an RA/Dec box.
  class FovMatcher
    Match = Struct.new(:object, :distance_arcmin, keyword_init: true)

    # width/height in degrees; rotation = sky position angle of the sensor's
    # "up" axis, degrees east of north (0 when unknown).
    def initialize(ra:, dec:, width:, height:, rotation: 0.0)
      @ra = ra.to_f
      @dec = dec.to_f
      @half_w = width.to_f / 2
      @half_h = height.to_f / 2
      @rotation = rotation.to_f * Coordinates::DEG
    end

    def matches(scope = AstroObject.all)
      radius = Math.hypot(@half_w, @half_h)
      candidates(scope, radius).filter_map do |object|
        x, y = project(object.ra_deg.to_f, object.dec_deg.to_f)
        next unless x

        # Rotate into the sensor frame.
        u = x * Math.cos(@rotation) - y * Math.sin(@rotation)
        v = x * Math.sin(@rotation) + y * Math.cos(@rotation)
        next unless u.abs <= @half_w && v.abs <= @half_h

        Match.new(object: object, distance_arcmin: (Coordinates.separation(@ra, @dec, object.ra_deg.to_f, object.dec_deg.to_f) * 60).round(2))
      end.sort_by(&:distance_arcmin)
    end

    private

    def candidates(scope, radius)
      scope = scope.where(dec_deg: (@dec - radius)..(@dec + radius))
      return scope.where.not(ra_deg: nil).to_a if (@dec.abs + radius) >= 89.0

      ra_half = radius / Math.cos(@dec * Coordinates::DEG)
      lo = @ra - ra_half
      hi = @ra + ra_half
      if lo < 0
        scope.where("ra_deg >= ? OR ra_deg <= ?", lo + 360, hi).to_a
      elsif hi >= 360
        scope.where("ra_deg >= ? OR ra_deg <= ?", lo, hi - 360).to_a
      else
        scope.where(ra_deg: lo..hi).to_a
      end
    end

    # Gnomonic projection (degrees on the tangent plane; x toward east, y north).
    def project(ra, dec)
      d = Coordinates::DEG
      dra = (ra - @ra) * d
      cos_c = Math.sin(@dec * d) * Math.sin(dec * d) + Math.cos(@dec * d) * Math.cos(dec * d) * Math.cos(dra)
      return nil if cos_c <= 0

      x = Math.cos(dec * d) * Math.sin(dra) / cos_c
      y = (Math.cos(@dec * d) * Math.sin(dec * d) - Math.sin(@dec * d) * Math.cos(dec * d) * Math.cos(dra)) / cos_c
      [ x / d, y / d ]
    end
  end
end
