# How the Hub shows coordinates everywhere (CoordinateParser reads them back):
#   RA           05h 35m 17.3s   (hours, minutes, seconds)
#   Dec          −05° 23′ 28″    (always signed)
#   Latitude     37° 18′ 00″ N
#   Longitude    121° 54′ 00″ W
#   Alt, az, separations, radii: decimal degrees, e.g. 62°
module CoordinateFormatter
  MINUS = "−"

  module_function

  def ra(degrees, seconds_digits: 1)
    return nil if degrees.nil?

    h, m, s = parts(degrees.to_f / 15.0, seconds_digits)
    h = 0 if h == 24
    format("%02dh %02dm %ss", h, m, seconds(s, seconds_digits))
  end

  def dec(degrees, seconds_digits: 0)
    return nil if degrees.nil?

    d, m, s = parts(degrees, seconds_digits)
    "#{degrees.to_f.negative? && [ d, m, s ].any?(&:positive?) ? MINUS : '+'}#{format('%02d° %02d′ %s″', d, m, seconds(s, seconds_digits))}"
  end

  def latitude(degrees)
    hemisphere(degrees, "N", "S")
  end

  def longitude(degrees)
    hemisphere(degrees, "E", "W")
  end

  # Altitude, azimuth, separations: whole degrees unless asked otherwise.
  def angle(degrees, digits: 0)
    return nil if degrees.nil?

    value = degrees.to_f.round(digits)
    text = digits.zero? ? value.to_i.to_s : format("%.#{digits}f", value)
    "#{text.sub(/\A-/, MINUS)}°"
  end

  # The decimal form, for tooltips and copying.
  def decimal(degrees, digits: 5)
    degrees.nil? ? nil : format("%.#{digits}f°", degrees.to_f)
  end

  def hemisphere(degrees, positive, negative)
    return nil if degrees.nil?

    d, m, s = parts(degrees, 0)
    format("%d° %02d′ %s″ %s", d, m, seconds(s, 0), degrees.to_f.negative? ? negative : positive)
  end

  # [whole units, minutes, seconds] of |value|, rounded as a whole so 59.96″
  # carries into the minutes.
  def parts(value, seconds_digits)
    total = (value.to_f.abs * 3600).round(seconds_digits)
    whole = (total / 3600).floor
    minutes = ((total - (whole * 3600)) / 60).floor
    [ whole, minutes, (total - (whole * 3600) - (minutes * 60)).round(seconds_digits) ]
  end

  def seconds(value, digits)
    digits.zero? ? format("%02d", value.round) : format("%0#{digits + 3}.#{digits}f", value)
  end
end
