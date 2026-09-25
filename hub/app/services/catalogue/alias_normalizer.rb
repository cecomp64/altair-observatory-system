module Catalogue
  # Normalises object names so "M 31", "m31" and "M-31" are the same key, and
  # "NGC0224" matches "NGC 224". Shared by search, import and the /config
  # alias lists.
  module AliasNormalizer
    CATALOG_PREFIXES = [
      [ /\Angc\d/, "NGC" ], [ /\Aic\d/, "IC" ], [ /\Am\d+\z/, "Messier" ], [ /\Aldn\d/, "LDN" ],
      [ /\Albn\d/, "LBN" ], [ /\A(sh2|sharpless)/, "Sharpless" ], [ /\Ac\d+\z/, "Caldwell" ],
      [ /\Aabell\d/, "Abell" ], [ /\Avdb\d/, "vdB" ], [ /\Aarp\d/, "Arp" ], [ /\Apgc\d/, "PGC" ], [ /\Augc\d/, "UGC" ]
    ].freeze

    module_function

    def normalize(name)
      return nil if name.nil?

      key = name.to_s.unicode_normalize(:nfkd).downcase.gsub(/[^a-z0-9+.]/, "")
      # Drop zero padding after a catalogue prefix: ngc0224 -> ngc224, m031 -> m31.
      key.sub(/\A([a-z]+)0+(?=\d)/, '\1').presence
    end

    def catalog_for(name)
      key = normalize(name)
      return nil if key.nil?

      CATALOG_PREFIXES.each { |pattern, catalog| return catalog if key.match?(pattern) }
      nil
    end
  end
end
