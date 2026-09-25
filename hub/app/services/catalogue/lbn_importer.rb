module Catalogue
  # Lynds Bright Nebulae (VizieR VII/9).
  class LbnImporter < Importer
    QUERY = 'SELECT Seq, "_RA.icrs" AS ra_icrs, "_DE.icrs" AS dec_icrs, Diam1, Bright FROM "VII/9/catalog"'.freeze

    private

    def source = "lbn"

    def each_record(content)
      vizier_rows(content).each do |row|
        number = row["seq"].to_s.strip
        ra = float(row["ra_icrs"])
        dec = float(row["dec_icrs"])
        next skip! if number.blank? || ra.nil? || dec.nil?

        yield({
          primary_name: "LBN #{number}",
          aliases: [ [ "LBN #{number}", "LBN" ] ],
          attributes: { ra_deg: ra.round(5), dec_deg: dec.round(5), object_type: "Bright Nebula",
                        size_major_arcmin: float(row["diam1"]), source_ref: "LBN #{number}" }
        })
      end
    end
  end
end
