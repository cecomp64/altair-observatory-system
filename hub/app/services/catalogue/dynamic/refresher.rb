module Catalogue
  module Dynamic
    # Brings a dynamic catalogue in line with its source. Objects merge into
    # the catalogue by name like an import (Importer#upsert). Entries for
    # objects still listed are kept, new ones added, and those no longer
    # listed get removed_at; a returning object starts a new listing. A failed
    # fetch raises before anything changes.
    class Refresher < Importer
      attr_reader :fetcher

      def initialize(catalogue, fetcher: catalogue.source.new)
        super()
        @catalogue = catalogue
        @fetcher = fetcher
      end

      def refresh
        records = @fetcher.fetch
        now = Time.current
        @result = Result.new(imported: 0, updated: 0, skipped: 0, errors: 0)
        @alias_index = ObjectAlias.pluck(:normalized_name, :astro_object_id).to_h
        counts = ActiveRecord::Base.transaction do
          listed = records.each_with_object({}) do |record, seen|
            object = upsert(primary_name: record.primary_name, aliases: record.aliases, attributes: record.attributes)
            seen[object.id] = record.details if object
          end
          sync_entries(listed, now)
        end
        result = counts.merge("new_objects" => @result.imported, "errors" => @result.errors)
        result.merge!(@fetcher.report) if @fetcher.respond_to?(:report)
        @catalogue.update!(refreshed_at: now, last_error: nil, last_result: result)
        result
      end

      private

      def source = @fetcher.class::SOURCE

      def sync_entries(listed, now)
        existing = @catalogue.entries.index_by(&:astro_object_id)
        added = 0
        listed.each do |object_id, details|
          entry = existing[object_id]
          if entry.nil?
            @catalogue.entries.create!(astro_object_id: object_id, first_seen_at: now, last_seen_at: now, details: details)
            added += 1
          elsif entry.active?
            entry.update!(last_seen_at: now, details: details)
          else
            entry.update!(first_seen_at: now, last_seen_at: now, removed_at: nil, details: details)
            added += 1
          end
        end
        gone = existing.values.select { |entry| entry.active? && !listed.key?(entry.astro_object_id) }
        DynamicCatalogueEntry.where(id: gone.map(&:id)).update_all(removed_at: now, updated_at: now)
        { "listed" => listed.size, "added" => added, "removed" => gone.size }
      end
    end
  end
end
