namespace :catalogue do
  desc "Import a catalogue: openngc, ldn, lbn or all. FILE=path imports a local copy instead of downloading."
  task :import, [ :catalogue ] => :environment do |_, args|
    catalogues = args[:catalogue].to_s == "all" || args[:catalogue].blank? ? Catalogue::Downloader::IMPORTERS.keys : [ args[:catalogue] ]
    catalogues.each do |catalogue|
      importer = Catalogue::Downloader::IMPORTERS.fetch(catalogue) { abort "Unknown catalogue #{catalogue.inspect}" }
      content = ENV["FILE"].present? ? File.read(ENV["FILE"]) : Catalogue::Downloader.fetch(catalogue)
      result = importer.import(content)
      puts "#{catalogue}: #{result.to_h.map { |k, v| "#{k} #{v}" }.join(', ')}"
    end
  end

  desc "Refresh a dynamic catalogue now (e.g. aavso_campaigns), or every configured one."
  task :refresh, [ :list ] => :environment do |_, args|
    keys = args[:list].present? ? [ args[:list] ] : Catalogue::Dynamic::SOURCES.select { |_, s| s.configured? }.keys
    abort "No dynamic catalogue is configured (set AAVSO_API_KEY)." if keys.empty?
    keys.each do |key|
      abort "Unknown list #{key.inspect}" unless Catalogue::Dynamic::SOURCES.key?(key)
      result = Catalogue::Dynamic::Refresher.new(DynamicCatalogue.for(key)).refresh
      puts "#{key}: #{result.map { |k, v| "#{k} #{v}" }.join(', ')}"
    end
  end
end
