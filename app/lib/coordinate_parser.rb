# Parses right ascension / declination entered by hand in either decimal
# form or sexagesimal ("HH:MM:SS" for RA, "+DD:MM:SS" for Dec), returning
# decimal degrees (or nil if the input can't be parsed).
module CoordinateParser
  SEXAGESIMAL = /\A([+-]?\d+)[:h\s]+(\d+)[:m\s]+([\d.]+)s?\z/

  module_function

  def parse_ra(input)
    value = parse(input)
    return nil if value.nil?

    # Bare sexagesimal RA is given in hours; convert to degrees.
    value = value * 15 if sexagesimal?(input)
    return nil unless (0...360).cover?(value)

    value
  end

  def parse_dec(input)
    value = parse(input)
    return nil if value.nil?
    return nil unless (-90..90).cover?(value)

    value
  end

  def parse(input)
    string = input.to_s.strip
    return nil if string.empty?

    if (match = SEXAGESIMAL.match(string))
      sign = match[1].to_s.start_with?("-") ? -1 : 1
      degrees_or_hours = match[1].to_f.abs
      minutes = match[2].to_f
      seconds = match[3].to_f
      sign * (degrees_or_hours + (minutes / 60.0) + (seconds / 3600.0))
    else
      Float(string)
    end
  rescue ArgumentError, TypeError
    nil
  end

  def sexagesimal?(input)
    SEXAGESIMAL.match?(input.to_s.strip)
  end
end
