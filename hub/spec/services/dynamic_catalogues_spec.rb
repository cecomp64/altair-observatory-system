require "rails_helper"

RSpec.describe "Dynamic catalogues" do
  def campaign(id, targets, start_date: "2025-01-01", end_date: "2027-01-01", state: "Active")
    { id: id, title: "Campaign #{id}", state: state, abstract: "", target: targets, start_date: start_date, end_date: end_date,
      requested_data_types: "Photometry", forum_url: "https://forums.aavso.org/t/#{id}", notes_public: "",
      created_at: "2025-01-01T00:00:00Z", updated_at: "2025-01-01T00:00:00Z" }
  end

  def star(name, ra: 333.510625, dec: 12.70313889)
    { auid: "000-#{name.parameterize}", name: name, ra: ra, dec: dec, magmin: 13.0, magmax: 9.5, vartype: "UGSS+ZZ:" }
  end

  def json(body = nil, status: 200, **fields) = [ status, { "Content-Type" => "application/json" }, (body || fields).to_json ]

  describe Catalogue::Dynamic::AavsoCampaigns do
    let(:apps_stubs) { Faraday::Adapter::Test::Stubs.new }
    let(:apps) { Faraday.new(url: "https://apps.aavso.org/v2/api/") { |f| f.adapter :test, apps_stubs } }
    let(:source) { described_class.new(api_key: nil, apps: apps, today: Date.new(2026, 10, 9), interval: 0) }
    let(:looked_up) { [] }
    let(:vizier_queries) { [] }

    before do
      # VizieR knows none of these stars unless a test says otherwise.
      allow(Catalogue::Downloader).to receive(:tap) { |query| vizier_queries << query and "" }
      apps_stubs.get("/v2/api/campaigns") do |env|
        expect(env.params).to include("active" => "true", "page_size" => "100")
        if env.params["page"] == "1"
          json(count: 4, next: "https://apps.aavso.org/v2/api/campaigns?active=true&page=2", previous: nil,
               results: [ campaign(929, [ "RU Peg" ]), campaign(880, [ "AD Leo", "RU Peg" ], end_date: nil),
                          campaign(813, [ "Old Nova" ], end_date: "2026-10-08") ])
        else
          json(count: 4, next: nil, previous: "…", results: [ campaign(950, [ "Future Star" ], start_date: "2026-11-01"),
                                                              campaign(951, [ "Unknown Star" ]) ])
        end
      end
      apps_stubs.get("/v2/api/stars/search/") do |env|
        looked_up << env.params["name"]
        case env.params["name"]
        when "RU Peg" then json(star("RU Peg"))
        when "AD Leo" then json(star("AD Leo", ra: 154.90, dec: 19.87))
        else json({ error: "Could not resolve #{env.params['name']} to a star." }, status: 404)
        end
      end
    end

    it "lists the stars of every campaign running today, across pages, with each star's campaigns" do
      records = source.fetch

      expect(records.map(&:primary_name)).to contain_exactly("RU Peg", "AD Leo")
      ru_peg = records.find { |r| r.primary_name == "RU Peg" }
      expect(ru_peg.attributes).to include(ra_deg: 333.510625, dec_deg: 12.70313889, magnitude: 9.5, object_type: "Variable Star")
      expect(ru_peg.details).to include("var_type" => "UGSS+ZZ:", "max_mag" => 9.5, "min_mag" => 13.0)
      expect(ru_peg.details["campaigns"].map { |c| [ c["id"], c["end_date"] ] }).to eq([ [ 929, "2027-01-01" ], [ 880, nil ] ])
      expect(source.skipped).to eq([ "Unknown Star" ]) # ended (813) and not yet started (950) campaigns aren't asked about
    end

    it "gets coordinates for every new star in one VizieR query, searching AAVSO only for the ones VizieR lacks" do
      allow(Catalogue::Downloader).to receive(:tap) do |query|
        vizier_queries << query
        <<~CSV
          #VizieR comment line
          OID,Name,RAJ2000,DEJ2000,Type,max,min,Period
          25135,RU Peg                        ,333.511,12.7031,UGSS+ZZ:   ,9.5,13.0,0.3746
          17089,AD Leo                        ,154.901,19.87,UV+BY      ,8.07,11.0,2.23
        CSV
      end

      records = source.fetch
      expect(vizier_queries.size).to eq(1)
      expect(vizier_queries.first).to include(%("B/vsx/vsx"), "'RU Peg'", "'AD Leo'", "'Unknown Star'")
      expect(looked_up).to eq([ "Unknown Star" ])
      ru_peg = records.find { |r| r.primary_name == "RU Peg" }
      expect(ru_peg.attributes).to include(ra_deg: 333.511, dec_deg: 12.7031, magnitude: 9.5, source_ref: "VSX 25135")
      expect(ru_peg.details).to include("var_type" => "UGSS+ZZ:", "period_days" => 0.3746)
    end

    it "falls back to the AAVSO star search when VizieR fails" do
      allow(Catalogue::Downloader).to receive(:tap).and_raise("VizieR query failed: HTTP 503")
      expect(source.fetch.map(&:primary_name)).to contain_exactly("RU Peg", "AD Leo")
      expect(looked_up).to contain_exactly("RU Peg", "AD Leo", "Unknown Star")
    end

    it "doesn't look up stars the catalogue already has with coordinates" do
      create(:astro_object, primary_name: "RU Pegasi").add_alias("RU Peg")
      source.fetch
      expect(looked_up).to contain_exactly("AD Leo", "Unknown Star")
      expect(vizier_queries.first).not_to include("'RU Peg'")
    end

    it "adds cadence and filters from the Target Tool when there is a key, and goes on without them if it fails" do
      tool_stubs = Faraday::Adapter::Test::Stubs.new
      tool = Faraday.new(url: "https://targettool.aavso.org/TargetTool/api/v1/") { |f| f.adapter :test, tool_stubs }
      tool_stubs.get("/TargetTool/api/v1/targets") { json(targets: [ target("RU Peg", obs_cadence: 1.0, filter: [ "V" ]) ]) }

      with_tool = described_class.new(api_key: "k", apps: apps, target_tool: tool, today: Date.new(2026, 10, 9), interval: 0)
      ru_peg = with_tool.fetch.find { |r| r.primary_name == "RU Peg" }
      expect(ru_peg.details).to include("cadence_days" => 1.0, "filters" => [ "V" ], "max_mag_band" => "V")

      failing_stubs = Faraday::Adapter::Test::Stubs.new { |s| s.get("/TargetTool/api/v1/targets") { [ 401, {}, "Invalid API key" ] } }
      failing = Faraday.new(url: "https://targettool.aavso.org/TargetTool/api/v1/") { |f| f.adapter :test, failing_stubs }
      records = described_class.new(api_key: "bad", apps: apps, target_tool: failing, today: Date.new(2026, 10, 9), interval: 0).fetch
      expect(records.map(&:primary_name)).to contain_exactly("RU Peg", "AD Leo")
    end

    it "defers the stars it can't look up once the star search is throttled" do
      throttled = Faraday.new(url: "https://apps.aavso.org/v2/api/") do |f|
        f.adapter :test, Faraday::Adapter::Test::Stubs.new { |stubs|
          stubs.get("/v2/api/campaigns") { json(count: 2, next: nil, results: [ campaign(929, [ "RU Peg", "AD Leo", "EV Lac" ]) ]) }
          stubs.get("/v2/api/stars/search/") do |env|
            looked_up << env.params["name"]
            env.params["name"] == "RU Peg" ? json(star("RU Peg")) : [ 429, { "Retry-After" => "1800" }, { detail: "Request was throttled." }.to_json ]
          end
        }
      end
      source = described_class.new(api_key: nil, apps: throttled, today: Date.new(2026, 10, 9), interval: 0)

      expect(source.fetch.map(&:primary_name)).to eq([ "RU Peg" ])
      expect(source).to have_attributes(deferred: [ "AD Leo", "EV Lac" ], retry_after: 1800, report: { "deferred" => 2 })
      expect(looked_up).to eq([ "RU Peg", "AD Leo" ]) # no more searches once throttled
    end

    it "waits the requested interval between apps API requests" do
      paced = described_class.new(api_key: nil, apps: apps, today: Date.new(2026, 10, 9), interval: 10)
      waits = []
      allow(paced).to receive(:sleep) { |seconds| waits << seconds }
      paced.fetch

      expect(waits.size).to eq(4) # 2 campaign pages + 3 star searches = 5 requests
      expect(waits).to all(be_within(0.5).of(10))
    end

    it "sends the apps token when there is one" do
      source = described_class.new(api_key: nil, apps_token: "t0k")
      expect(source.instance_variable_get(:@apps).headers["Authorization"]).to eq("Token t0k")
    end

    it "raises when the campaigns API fails or is throttled, so nothing changes" do
      throttled = Faraday.new(url: "https://apps.aavso.org/v2/api/") do |f|
        f.adapter :test, Faraday::Adapter::Test::Stubs.new { |s| s.get("/v2/api/campaigns") { [ 429, { "Retry-After" => "3399" }, "{}" ] } }
      end
      expect { described_class.new(api_key: nil, apps: throttled, interval: 0).fetch }
        .to raise_error(described_class::Throttled, "AAVSO campaigns rate limited; retry in 57 min")


      broken = Faraday.new(url: "https://apps.aavso.org/v2/api/") do |f|
        f.adapter :test, Faraday::Adapter::Test::Stubs.new { |s| s.get("/v2/api/campaigns") { [ 503, {}, "down" ] } }
      end
      expect { described_class.new(api_key: nil, apps: broken, interval: 0).fetch }.to raise_error(/campaigns HTTP 503/)
    end
  end

  def target(name, ra: 291.13779, dec: 50.24142, **extra)
    { star_name: name, ra: ra, dec: dec, var_type: "ZAND+SR", max_mag: 5.6, max_mag_band: "V", min_mag: 10.1,
      min_mag_band: "V", period: nil, obs_cadence: 3.0, obs_mode: "All", obs_section: [ "ac" ], filter: [ "V", "B" ],
      other_info: "", last_data_point: 1_518_435_383, priority: false, constellation: "Cyg",
      observability_times: [], solar_conjunction: false }.merge(extra)
  end

  describe Catalogue::Dynamic::Refresher do
    let(:catalogue) { DynamicCatalogue.for("aavso_campaigns") }
    let(:fetcher) { Catalogue::Dynamic::AavsoCampaigns.new(api_key: nil) }

    def refresh_with(*names)
      records = names.map do |name|
        Catalogue::Dynamic::Record.new(primary_name: name, aliases: [], details: { "star_name" => name },
                                       attributes: { ra_deg: 10, dec_deg: 20, object_type: "Variable Star", source_ref: name })
      end
      allow(fetcher).to receive(:fetch).and_return(records)
      described_class.new(catalogue, fetcher: fetcher).refresh
    end

    it "adds new targets, keeps listed ones and ends the listing of those that drop off" do
      travel_to(Time.zone.parse("2026-10-01 12:00")) { expect(refresh_with("CH Cyg", "T CrB")).to include("listed" => 2, "added" => 2, "removed" => 0, "new_objects" => 2) }
      ch_cyg = AstroObject.find_by_alias("CH Cyg")
      expect(ch_cyg).to have_attributes(source: "aavso", object_type: "Variable Star")

      travel_to(Time.zone.parse("2026-10-02 12:00")) { expect(refresh_with("T CrB", "V1405 Cas")).to include("added" => 1, "removed" => 1) }
      expect(catalogue.active_entries.map { |e| e.astro_object.primary_name }).to contain_exactly("T CrB", "V1405 Cas")
      expect(catalogue.entries.find_by(astro_object: ch_cyg).removed_at).to eq(Time.zone.parse("2026-10-02 12:00"))
      expect(ch_cyg.reload).to be_persisted # objects stay in the catalogue
      expect(catalogue.reload).to have_attributes(refreshed_at: Time.zone.parse("2026-10-02 12:00"), last_error: nil)

      travel_to(Time.zone.parse("2026-10-05 12:00")) { refresh_with("CH Cyg", "T CrB", "V1405 Cas") }
      entry = catalogue.entries.find_by(astro_object: ch_cyg)
      expect(entry).to have_attributes(removed_at: nil, first_seen_at: Time.zone.parse("2026-10-05 12:00"))
      expect(AstroObject.where(source: "aavso").count).to eq(3)
    end

    it "lists an object the catalogue already knows under the same name" do
      existing = create(:astro_object, primary_name: "T Coronae Borealis", source: "telescopius", magnitude: 2.0).tap { |o| o.add_alias("T CrB") }
      refresh_with("T CrB")

      expect(catalogue.active_entries.map(&:astro_object)).to eq([ existing ])
      expect(existing.reload).to have_attributes(source: "telescopius", magnitude: 2.0)
    end

    it "changes nothing when the fetch fails" do
      refresh_with("CH Cyg")
      allow(fetcher).to receive(:fetch).and_raise("AAVSO campaigns HTTP 503")

      expect { described_class.new(catalogue, fetcher: fetcher).refresh }.to raise_error(/503/)
      expect(catalogue.active_entries.count).to eq(1)
    end
  end

  describe DynamicCatalogueRefreshJob do
    it "tries again after the rate limit when part of the list was deferred" do
      allow_any_instance_of(Catalogue::Dynamic::AavsoCampaigns).to receive(:fetch) do |source|
        source.instance_variable_set(:@deferred, [ "AD Leo" ])
        source.instance_variable_set(:@retry_after, 1800)
        []
      end

      expect { described_class.perform_now("aavso_campaigns") }
        .to have_enqueued_job(described_class).with("aavso_campaigns").at(a_value_within(5.seconds).of(30.minutes.from_now))
      expect(DynamicCatalogue.for("aavso_campaigns").last_result).to include("deferred" => 1)
    end


    it "refreshes configured lists only, and records a failure on the list" do
      allow(Catalogue::Dynamic::AavsoCampaigns).to receive(:configured?).and_return(false)
      expect { described_class.perform_now }.not_to change(DynamicCatalogue, :count)

      allow_any_instance_of(Catalogue::Dynamic::AavsoCampaigns).to receive(:fetch).and_raise("AAVSO campaigns HTTP 503")
      described_class.perform_now("aavso_campaigns")
      expect(DynamicCatalogue.for("aavso_campaigns")).to have_attributes(last_error: "AAVSO campaigns HTTP 503", refreshed_at: nil)
      expect(DynamicCatalogue.for("aavso_campaigns").attempted_at).to be_present
    end
  end
end
