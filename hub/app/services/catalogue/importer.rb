require "csv"

module Catalogue
  # Base for catalogue importers (port of astrophotography-database
  # catalogue_importer.py). An incoming object is merged into an existing one
  # when any of its names already exists as a normalised alias; otherwise it
  # is created. Aliases are deduplicated per object.
  class Importer
    Result = Struct.new(:imported, :updated, :skipped, :errors, keyword_init: true) do
      def to_h = super.transform_keys(&:to_s)
    end

    def self.import(content)
      new.import(content)
    end

    def import(content)
      @result = Result.new(imported: 0, updated: 0, skipped: 0, errors: 0)
      @alias_index = ObjectAlias.pluck(:normalized_name, :astro_object_id).to_h
      ActiveRecord::Base.transaction { each_record(content) { |attrs| upsert(**attrs) } }
      @result
    end

    private

    # Yields { primary_name:, aliases: [[name, catalog], ...], attributes: {...} }
    # or nil (skipped row).
    def each_record(_content)
      raise NotImplementedError
    end

    def source
      raise NotImplementedError
    end

    def upsert(primary_name:, aliases:, attributes:)
      names = [ [ primary_name, nil ], *aliases ]
      existing_id = names.lazy.map { |name, _| @alias_index[AliasNormalizer.normalize(name)] }.find(&:itself)

      if existing_id
        object = AstroObject.find(existing_id)
        # Fill gaps only; never overwrite what another catalogue already set.
        fill = attributes.select { |key, value| value.present? && object[key].blank? }
        object.update!(fill) if fill.any?
        @result.updated += 1
      else
        object = AstroObject.create!(primary_name: primary_name, source: source, **attributes)
        @alias_index[AliasNormalizer.normalize(primary_name)] = object.id
        @result.imported += 1
      end

      names.each do |name, catalog|
        object.add_alias(name, catalog: catalog)
        normalized = AliasNormalizer.normalize(name)
        @alias_index[normalized] ||= object.id if normalized
      end
    rescue ActiveRecord::RecordInvalid => e
      Rails.logger.warn("[catalogue] #{self.class.name}: #{primary_name}: #{e.message}")
      @result.errors += 1
    end

    def skip!
      @result.skipped += 1
      nil
    end

    def float(value)
      Float(value.to_s.strip)
    rescue ArgumentError, TypeError
      nil
    end

    # VizieR CSV comes with "#" comment lines.
    def vizier_rows(content)
      CSV.parse(content.lines.reject { |l| l.start_with?("#") }.join, headers: true, header_converters: ->(h) { h.to_s.strip.downcase })
    end
  end
end
