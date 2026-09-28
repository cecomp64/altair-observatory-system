# Parses a horizon file: a plain text/CSV file of "azimuth,altitude" pairs
# (degrees, comma or whitespace separated) giving the minimum altitude that is
# clear of obstructions at each azimuth. Blank lines and lines starting with
# "#" are skipped, as is a header row (a first line that doesn't start with a
# number); columns after the second are ignored.
#
# Returns [az, alt] floats sorted by azimuth, or raises HorizonFileParser::Error
# with a message naming the first bad line.
module HorizonFileParser
  class Error < StandardError; end

  MAX_BYTES = 1.megabyte
  AZIMUTH = 0..360
  ALTITUDE = -90..90

  module_function

  def parse(text)
    raise Error, "is larger than #{MAX_BYTES / 1.megabyte} MB" if text.bytesize > MAX_BYTES

    points = []
    header_allowed = true
    text.b.delete_prefix("\xEF\xBB\xBF".b).each_line.with_index(1) do |line, line_no|
      line = line.strip
      next if line.empty? || line.start_with?("#")

      fields = line.split(/[,;\s]+/)
      az = number(fields[0])
      if az.nil? && header_allowed
        header_allowed = false
        next
      end
      header_allowed = false

      alt = number(fields[1])
      raise Error, "line #{line_no}: expected an azimuth and an altitude" if az.nil? || alt.nil?
      raise Error, "line #{line_no}: azimuth must be between #{AZIMUTH.begin} and #{AZIMUTH.end}" unless AZIMUTH.cover?(az)
      raise Error, "line #{line_no}: altitude must be between #{ALTITUDE.begin} and #{ALTITUDE.end}" unless ALTITUDE.cover?(alt)

      points << [ az, alt ]
    end
    raise Error, "has no azimuth,altitude pairs" if points.empty?

    points.sort_by(&:first)
  end

  def number(field)
    Float(field, exception: false)&.then { |value| value.finite? ? value : nil }
  end
end
