module Catalogue
  # OpenNGC (NGC + IC, with Messier numbers and common names).
  class OpenNgcImporter < Importer
    URL = "https://raw.githubusercontent.com/mattiaverga/OpenNGC/refs/heads/master/database_files/NGC.csv".freeze

    # OpenNGC type codes → readable types.
    TYPES = {
      "*" => "Star", "**" => "Double Star", "*Ass" => "Association", "OCl" => "Open Cluster",
      "GCl" => "Globular Cluster", "Cl+N" => "Cluster + Nebula", "G" => "Galaxy", "GPair" => "Galaxy Pair",
      "GTrpl" => "Galaxy Triplet", "GGroup" => "Galaxy Group", "PN" => "Planetary Nebula", "HII" => "HII Region",
      "DrkN" => "Dark Nebula", "EmN" => "Emission Nebula", "Neb" => "Nebula", "RfN" => "Reflection Nebula",
      "SNR" => "Supernova Remnant", "Nova" => "Nova", "NonEx" => "Nonexistent", "Dup" => "Duplicate", "Other" => "Other"
    }.freeze

    private

    def source = "openngc"

    def each_record(content)
      CSV.parse(content, col_sep: ";", headers: true, liberal_parsing: true).each do |row|
        record = parse(row)
        yield record if record
      rescue StandardError => e
        Rails.logger.debug { "[catalogue] OpenNGC row #{row['Name']}: #{e.message}" }
        @result.errors += 1
      end
    end

    def parse(row)
      name = row["Name"].to_s.strip
      match = name.match(/\A(NGC|IC)0*(\d+.*)\z/)
      return skip! unless match
      # Duplicates and nonexistent entries only point at other rows.
      return skip! if %w[Dup NonEx].include?(row["Type"].to_s.strip)

      ra = CoordinateParser.parse_ra(row["RA"])
      dec = CoordinateParser.parse_dec(row["Dec"])
      return skip! if ra.nil? || dec.nil?

      designation = "#{match[1]} #{match[2]}"
      aliases = [ [ designation, match[1] ] ]
      messier = row["M"].to_s.strip.sub(/\A0+/, "")
      aliases << [ "M #{messier}", "Messier" ] if messier.present?
      common = row["Common names"].to_s.split(",").map(&:strip).reject(&:blank?)
      common.each { |c| aliases << [ c, "Common" ] }

      primary = common.first || (messier.present? ? "M #{messier}" : designation)
      {
        primary_name: primary,
        aliases: aliases,
        attributes: {
          ra_deg: ra.round(5), dec_deg: dec.round(5),
          object_type: TYPES.fetch(row["Type"].to_s.strip, row["Type"].presence),
          magnitude: float(row["V-Mag"]) || float(row["B-Mag"]),
          size_major_arcmin: float(row["MajAx"]), size_minor_arcmin: float(row["MinAx"]),
          position_angle_deg: float(row["PosAng"]), constellation: row["Const"].presence,
          source_ref: name
        }
      }
    end
  end
end
