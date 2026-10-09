require "csv"

module Catalogue
  module Dynamic
    # Stars in the AAVSO's active observing campaigns (Alerts & Campaigns).
    #
    # - Which stars, and the campaign windows: the AAVSO apps API
    #   (https://apps.aavso.org/v2/api/schema/), GET campaigns?active=true,
    #   every page. A campaign counts only while today is within its dates (no
    #   end date = ongoing), so a star's listing ends with its last campaign.
    # - Coordinates, only for stars the catalogue doesn't already have with
    #   them: one VizieR TAP query of VSX (B/vsx/vsx; campaign targets use VSX
    #   names) for all of them, then the apps API's stars/search for the few
    #   VizieR lacks (objects newer than its copy). VizieR positions are to
    #   0.001 deg.
    # - Cadence, filters, mode and priority: the Target Tool API
    #   (https://targettool.aavso.org/TargetTool/api), when AAVSO_API_KEY (or
    #   credentials aavso.api_key) is set. Optional: a failure there is logged
    #   and the refresh goes on without it.
    #
    # A refresh is ~1-4 apps requests for the campaign list, one VizieR query
    # and one Target Tool request, plus a star search per star VizieR lacks.
    # Both apps endpoints answer without a token. AAVSO asks for no more than
    # one request every 10 seconds (https://docs.aavso.org/overview-1), so
    # apps requests are paced. Anonymous use is also rate
    # limited (HTTP 429 with Retry-After; the quota isn't published).
    # AAVSO_APPS_TOKEN (or credentials aavso.apps_token, from Settings at
    # https://apps.aavso.org/v2/) is sent as "Authorization: Token ..."; the
    # docs don't say whether it raises that quota, and the 10-second pacing
    # applies either way. If
    # star searches are throttled, the stars not yet looked up are deferred:
    # everything else is listed, and DynamicCatalogueRefreshJob tries again
    # after Retry-After. Stars already in the catalogue need no search, so a
    # deferral never ends a listing.
    class AavsoCampaigns
      KEY = "aavso_campaigns".freeze
      NAME = "AAVSO Alerts & Campaigns".freeze
      SHORT_NAME = "AAVSO".freeze
      DESCRIPTION = "Variable stars in the AAVSO's active observing campaigns.".freeze
      SOURCE = "aavso".freeze
      SETUP = "nothing required. AAVSO_API_KEY (a Target Tool key) adds cadence and filters; AAVSO_APPS_TOKEN authenticates to the campaigns API.".freeze
      APPS_URL = ENV.fetch("AAVSO_APPS_API_URL", "https://apps.aavso.org/v2/api")
      TARGET_TOOL_URL = ENV.fetch("AAVSO_TARGET_TOOL_URL", "https://targettool.aavso.org/TargetTool/api/v1")
      CAMPAIGN_URL = "https://apps.aavso.org/v2/campaigns/%d".freeze
      MAX_PAGES = 50
      PAGE_SIZE = 100 # asked for; the API may keep its own (10)
      VIZIER_BATCH = 200 # names per VizieR query
      REQUEST_INTERVAL = 10 # seconds between apps API requests

      Throttled = Class.new(StandardError) do
        attr_reader :retry_after

        def initialize(path, retry_after)
          @retry_after = retry_after
          super("AAVSO #{path} rate limited; retry in #{(retry_after / 60.0).ceil} min")
        end
      end

      def self.api_key
        ENV["AAVSO_API_KEY"].presence || Rails.application.credentials.dig(:aavso, :api_key)
      end

      def self.apps_token
        ENV["AAVSO_APPS_TOKEN"].presence || Rails.application.credentials.dig(:aavso, :apps_token)
      end

      # The campaigns API is public.
      def self.configured? = true

      # Star names from the last fetch that couldn't be listed: unknown to the
      # star search (skipped), or not looked up because it was throttled
      # (deferred, with retry_after seconds).
      attr_reader :skipped, :deferred, :retry_after

      def initialize(api_key: self.class.api_key, apps_token: self.class.apps_token, apps: nil, target_tool: nil,
                     vizier: ->(query) { Downloader.tap(query) }, today: Date.current, interval: REQUEST_INTERVAL)
        @api_key = api_key
        @vizier = vizier
        @today = today
        @interval = interval
        @skipped = []
        @deferred = []
        @apps = apps || Faraday.new(url: APPS_URL, request: { timeout: 30 }) do |f|
          f.headers["Accept"] = "application/json"
          f.headers["User-Agent"] = "AltairObservatoryHub (dynamic catalogues)"
          f.headers["Authorization"] = "Token #{apps_token}" if apps_token.present?
        end
        @target_tool = target_tool || (@api_key.present? && Faraday.new(url: TARGET_TOOL_URL, request: { timeout: 60 }) do |f|
          f.request :authorization, :basic, @api_key, "api_token"
          f.headers["Accept"] = "application/json"
        end)
      end

      def fetch
        stars = {}
        active_campaigns.each do |campaign|
          Array(campaign["target"]).each do |name|
            name = name.to_s.squish
            key = AliasNormalizer.normalize(name)
            next if key.nil?

            (stars[key] ||= { name: name, campaigns: [] })[:campaigns] << summary(campaign)
          end
        end
        known = stars.select { |_, star| AstroObject.find_by_alias(star[:name])&.coordinates? }.keys
        @vsx = vsx_positions(stars.except(*known).values.pluck(:name))
        targets = target_tool_targets
        stars.filter_map { |key, star| record(key, star[:name], star[:campaigns], targets[key], known: known.include?(key)) }
      end

      # Counts for the refresh result.
      def report
        { "skipped" => skipped.size, "deferred" => deferred.size }.select { |_, n| n.positive? }
      end

      private

      # Every page of campaigns?active=true, keeping those running today.
      def active_campaigns
        campaigns = []
        page = 1
        loop do
          data = get_json(@apps, "campaigns", { active: true, page: page, page_size: PAGE_SIZE }, paced: true)
          raise "AAVSO campaigns API returned no results list" unless data["results"].is_a?(Array)

          campaigns.concat(data["results"])
          break if data["next"].blank?
          raise "AAVSO campaigns API: more than #{MAX_PAGES} pages" if (page += 1) > MAX_PAGES
        end
        campaigns.select { |c| running?(c) }
      end

      def running?(campaign)
        return false if campaign["state"].present? && campaign["state"] != "Active"

        start = date(campaign["start_date"])
        finish = date(campaign["end_date"])
        (start.nil? || start <= @today) && (finish.nil? || finish >= @today)
      end

      def summary(campaign)
        {
          "id" => campaign["id"], "title" => campaign["title"].to_s.squish,
          "start_date" => campaign["start_date"], "end_date" => campaign["end_date"],
          "data_types" => campaign["requested_data_types"].presence, "forum_url" => campaign["forum_url"].presence,
          "notes" => campaign["notes_public"].presence
        }.compact
      end

      def record(key, name, campaigns, target, known:)
        star = nil
        unless known
          star = @vsx[key] || star_search(name)
          return nil if star.nil?
        end

        attributes = { object_type: "Variable Star", source_ref: star&.dig("auid") || star&.dig("oid")&.then { |oid| "VSX #{oid}" } }
        if star
          attributes.merge!(ra_deg: star["ra"].to_f % 360, dec_deg: star["dec"].to_f, magnitude: number(star["magmax"]))
        end
        aliases = star && star["name"].present? && star["name"] != name ? [ [ star["name"], nil ] ] : []
        Record.new(primary_name: name, aliases: aliases, attributes: attributes.compact,
                   details: details(name, campaigns, star, target))
      end

      def details(name, campaigns, star, target)
        details = {
          "star_name" => name, "auid" => star&.dig("auid"), "var_type" => star&.dig("vartype"),
          "max_mag" => number(star&.dig("magmax")), "min_mag" => number(star&.dig("magmin")),
          "period_days" => number(star&.dig("period")),
          "campaigns" => campaigns.sort_by { |c| -c["id"].to_i }
        }
        if target
          details.merge!(
            "var_type" => target["var_type"].presence || details["var_type"],
            "max_mag" => number(target["max_mag"]) || details["max_mag"], "max_mag_band" => target["max_mag_band"].presence,
            "min_mag" => number(target["min_mag"]) || details["min_mag"], "min_mag_band" => target["min_mag_band"].presence,
            "period_days" => number(target["period"]) || details["period_days"], "cadence_days" => number(target["obs_cadence"]),
            "obs_mode" => target["obs_mode"].presence, "filters" => Array(target["filter"]).reject(&:blank?).presence,
            "priority" => target["priority"] == true || nil, "other_info" => target["other_info"].presence
          )
        end
        details.compact
      end

      # VSX positions from VizieR for these names, by normalised name, in the
      # star search's shape. Empty if VizieR fails: the star search covers them.
      def vsx_positions(names)
        names.each_slice(VIZIER_BATCH).each_with_object({}) do |batch, found|
          list = batch.map { |n| "'#{n.gsub("'", "''")}'" }.join(", ")
          csv = @vizier.call(%(SELECT "OID", "Name", "RAJ2000", "DEJ2000", "Type", "max", "min", "Period" FROM "B/vsx/vsx" WHERE "Name" IN (#{list})))
          CSV.parse(csv.lines.reject { |l| l.start_with?("#") }.join, headers: true).each do |row|
            name = row["Name"].to_s.squish
            next unless number(row["RAJ2000"]) && number(row["DEJ2000"])

            found[AliasNormalizer.normalize(name)] = {
              "name" => name, "oid" => row["OID"].to_s.strip.presence, "ra" => row["RAJ2000"], "dec" => row["DEJ2000"],
              "vartype" => row["Type"].to_s.strip.presence, "magmax" => row["max"], "magmin" => row["min"],
              "period" => row["Period"]
            }
          end
        end
      rescue StandardError => e
        Rails.logger.warn("[catalogue] VizieR VSX positions (falling back to AAVSO star search): #{e.message}")
        {}
      end

      # The star search answers 404 for a name it can't resolve. Once it is
      # throttled, the rest of this fetch's searches are deferred.
      def star_search(name)
        if @retry_after
          @deferred << name
          return nil
        end

        pace
        response = @apps.get("stars/search/", { name: name })
        if response.status == 429
          @retry_after = retry_after_seconds(response)
          @deferred << name
          return nil
        end
        raise "AAVSO star search HTTP #{response.status} for #{name}" unless response.success? || response.status == 404

        star = response.success? ? JSON.parse(response.body) : {}
        return star if number(star["ra"]) && number(star["dec"])

        @skipped << name
        nil
      end

      # Target Tool entries for the Alerts & Campaigns section, by normalised
      # star name; empty without a key or if the Target Tool fails.
      def target_tool_targets
        return {} unless @target_tool

        targets = get_json(@target_tool, "targets", { obs_section: "ac" })["targets"]
        Array(targets).index_by { |t| AliasNormalizer.normalize(t["star_name"].to_s) }.except(nil)
      rescue StandardError => e
        Rails.logger.warn("[catalogue] AAVSO Target Tool (cadence and filters skipped): #{e.message}")
        {}
      end

      def get_json(connection, path, params, paced: false)
        pace if paced
        response = connection.get(path, params)
        raise Throttled.new(path, retry_after_seconds(response)) if response.status == 429
        raise "AAVSO #{path} HTTP #{response.status}: #{response.body.to_s.truncate(200)}" unless response.success?

        JSON.parse(response.body)
      end

      # Waits until REQUEST_INTERVAL has passed since the last apps request.
      def pace
        now = Process.clock_gettime(Process::CLOCK_MONOTONIC)
        wait = @last_request_at ? @last_request_at + @interval - now : 0
        sleep(wait) if wait.positive?
        @last_request_at = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      end

      def retry_after_seconds(response)
        Integer(response.headers["Retry-After"].to_s, exception: false)&.clamp(60, 6.hours.to_i) || 1.hour.to_i
      end

      def date(value)
        Date.iso8601(value.to_s)
      rescue Date::Error
        nil
      end

      def number(value)
        Float(value.to_s.strip)
      rescue ArgumentError, TypeError
        nil
      end
    end
  end
end
