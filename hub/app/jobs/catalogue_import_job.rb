# Downloads and imports a catalogue (openngc, ldn, lbn). Progress and the
# result are kept in the cache for the admin catalogue page.
class CatalogueImportJob < ApplicationJob
  queue_as :default

  def self.status_key(catalogue) = "catalogue-import/#{catalogue}"

  def perform(catalogue)
    Rails.cache.write(self.class.status_key(catalogue), { "state" => "running", "at" => Time.current.iso8601 })
    content = Catalogue::Downloader.fetch(catalogue)
    result = Catalogue::Downloader::IMPORTERS.fetch(catalogue).import(content)
    Rails.cache.write(self.class.status_key(catalogue), { "state" => "done", "at" => Time.current.iso8601, "result" => result.to_h })
  rescue StandardError => e
    Rails.cache.write(self.class.status_key(catalogue), { "state" => "failed", "at" => Time.current.iso8601, "error" => e.message })
    raise
  end
end
