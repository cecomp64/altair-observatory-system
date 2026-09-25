module Catalogue
  # Resolves an object name (port of name_resolver.py): the local catalogue
  # first, then Telescopius, whose answer is stored so the next lookup is
  # local. Failed remote lookups are cached for a day.
  class NameResolver
    def initialize(client: nil)
      @client = client
    end

    # Returns [astro_object, :local | :telescopius] or [nil, reason].
    def resolve(name, created_by: nil)
      name = name.to_s.strip
      return [ nil, :blank ] if name.blank?

      local = AstroObject.find_by_alias(name)
      return [ local, :local ] if local

      client = @client || (TelescopiusClient.new if TelescopiusClient.configured?)
      return [ nil, :not_configured ] unless client

      cache_key = "catalogue/telescopius-miss/#{AliasNormalizer.normalize(name)}"
      return [ nil, :not_found ] if Rails.cache.read(cache_key)

      result = client.search(name)
      unless result&.ra_deg && result.dec_deg
        Rails.cache.write(cache_key, true, expires_in: 1.day)
        return [ nil, :not_found ]
      end

      [ store(result, name, created_by), :telescopius ]
    rescue StandardError => e
      Rails.logger.warn("[catalogue] Telescopius lookup for #{name.inspect} failed: #{e.message}")
      [ nil, :error ]
    end

    private

    def store(result, query, created_by)
      # Another name for an object we already have: merge instead of duplicating.
      existing = ([ result.name ] + result.aliases).lazy.map { |n| AstroObject.find_by_alias(n) }.find(&:itself)
      object = existing || AstroObject.create!(
        primary_name: result.name, ra_deg: result.ra_deg.round(5), dec_deg: result.dec_deg.round(5),
        object_type: result.object_type, magnitude: result.magnitude, constellation: result.constellation,
        source: "telescopius", created_by: created_by
      )
      ([ query ] + result.aliases).each { |n| object.add_alias(n) }
      object
    end
  end
end
