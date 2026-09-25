module Catalogue
  # Fetches catalogue sources: OpenNGC from GitHub, LDN/LBN via VizieR TAP.
  module Downloader
    VIZIER_TAP_URL = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync".freeze
    IMPORTERS = { "openngc" => OpenNgcImporter, "ldn" => LdnImporter, "lbn" => LbnImporter }.freeze

    module_function

    def fetch(catalogue)
      case catalogue
      when "openngc" then get(OpenNgcImporter::URL)
      when "ldn" then tap(LdnImporter::QUERY)
      when "lbn" then tap(LbnImporter::QUERY)
      else raise ArgumentError, "unknown catalogue #{catalogue.inspect}"
      end
    end

    def get(url)
      response = connection.get(url)
      raise "GET #{url} failed: HTTP #{response.status}" unless response.success?

      response.body.force_encoding("UTF-8")
    end

    def tap(query)
      response = connection.post(VIZIER_TAP_URL, { REQUEST: "doQuery", LANG: "ADQL", FORMAT: "csv", QUERY: query })
      raise "VizieR query failed: HTTP #{response.status}" unless response.success?

      response.body.force_encoding("UTF-8")
    end

    def connection
      Faraday.new(request: { timeout: 180 }) do |f|
        f.request :url_encoded
        f.response :follow_redirects if defined?(Faraday::FollowRedirects)
      end
    end
  end
end
