# Parses coordinates entered by hand, in decimal or sexagesimal form, into
# decimal degrees (nil when the input can't be parsed or is out of range).
# It accepts what CoordinateFormatter prints, so displayed values can be
# pasted back in:
#   RA   83.822 · 05:35:17 · 05h 35m 17.3s · 5.5881h (hours)
#   Dec  -5.391 · -05:23:28 · −05° 23′ 28″ · -5d23m28s
#   Lat  37.3 · 37° 18′ 00″ N · 37:18:00          (S is negative)
#   Long -121.9 · 121° 54′ 00″ W · -121:54:00     (W is negative)
module CoordinateParser
  NUMBER = /\d+(?:\.\d+)?/
  # One to three numbers split by any mix of : h d m s ° ′ ″ ' " or spaces.
  SEXAGESIMAL = /\A([+-])?\s*(#{NUMBER})(?:\s*[:hdm°′'\s]\s*(#{NUMBER})(?:\s*[:ms′'\s]\s*(#{NUMBER}))?)?\s*[dhms°′″'"]?\z/i

  module_function

  def parse_ra(input)
    string = normalize(input)
    value = parse(string)
    return nil if value.nil?

    # Sexagesimal RA, or a number marked h, is in hours.
    value *= 15 if sexagesimal?(string) || string.match?(/\A[+-]?#{NUMBER}\s*h\z/i)
    value = 0.0 if value == 360.0
    (0...360).cover?(value) ? value : nil
  end

  def parse_dec(input)
    value = parse(normalize(input))
    value && (-90..90).cover?(value) ? value : nil
  end

  def parse_latitude(input)
    parse_with_hemisphere(input, "N", "S", 90)
  end

  def parse_longitude(input)
    parse_with_hemisphere(input, "E", "W", 180)
  end

  def parse(input)
    string = normalize(input)
    return nil if string.empty?

    match = SEXAGESIMAL.match(string.sub(/\s*h\z/i, ""))
    return nil unless match

    sign = match[1] == "-" ? -1 : 1
    sign * (match[2].to_f + (match[3].to_f / 60.0) + (match[4].to_f / 3600.0))
  end

  # More than one component (05:35:17, 05h 35m): not a plain decimal.
  def sexagesimal?(input)
    match = SEXAGESIMAL.match(normalize(input))
    match.present? && match[3].present?
  end

  def normalize(input)
    input.to_s.dup.force_encoding(Encoding::UTF_8).scrub("").strip.tr("−–", "--")
  end

  def parse_with_hemisphere(input, positive, negative, limit)
    string = normalize(input)
    sign = 1
    if (hemisphere = string[/\s*([#{positive}#{negative}])\z/i, 1])
      return nil if string.start_with?("-", "+")

      sign = hemisphere.casecmp?(negative) ? -1 : 1
      string = string.sub(/\s*[#{positive}#{negative}]\z/i, "")
    end
    value = parse(string)
    value && (-limit..limit).cover?(value * sign) ? value * sign : nil
  end
end
