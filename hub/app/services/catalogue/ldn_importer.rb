module Catalogue
  # Lynds Dark Nebulae (VizieR VII/7A).
  class LdnImporter < Importer
    QUERY = 'SELECT LDN, "_RA.icrs" AS ra_icrs, "_DE.icrs" AS dec_icrs, Area, Opacity FROM "VII/7A/ldn"'.freeze

    private

    def source = "ldn"

    def each_record(content)
      vizier_rows(content).each do |row|
        number = row["ldn"].to_s.strip.sub(/\ALDN\s*/i, "")
        ra = float(row["ra_icrs"])
        dec = float(row["dec_icrs"])
        next skip! if number.blank? || ra.nil? || dec.nil?

        yield({
          primary_name: "LDN #{number}",
          aliases: [ [ "LDN #{number}", "LDN" ] ],
          attributes: { ra_deg: ra.round(5), dec_deg: dec.round(5), object_type: "Dark Nebula",
                        size_major_arcmin: float(row["area"]), source_ref: "LDN #{number}" }
        })
      end
    end
  end
end
