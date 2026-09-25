require "rails_helper"

RSpec.describe Catalogue::TelescopiusClient do
  let(:stubs) { Faraday::Adapter::Test::Stubs.new }
  let(:connection) { Faraday.new(url: "https://api.telescopius.com/v2.0/") { |f| f.adapter :test, stubs } }
  let(:client) { described_class.new(api_key: "k", connection: connection) }

  it "parses the first search result, converting RA from hours" do
    stubs.get("/v2.0/targets/search") do |env|
      expect(env.params).to include("name" => "M31")
      [ 200, { "Content-Type" => "application/json" }, {
        page_results: [ { object: {
          main_id: "NGC 224", main_name: "Andromeda Galaxy", ids: [ "NGC 224", "M 31" ], names: [ "Andromeda Galaxy" ],
          types: [ "gxy" ], ra: 0.71231, dec: 41.2689, visual_mag: 3.4, con_name: "Andromeda"
        } } ]
      }.to_json ]
    end

    result = client.search("M31")
    expect(result).to have_attributes(name: "Andromeda Galaxy", object_type: "Galaxy", constellation: "Andromeda", magnitude: 3.4)
    expect(result.ra_deg).to be_within(0.001).of(10.685)
    expect(result.aliases).to include("NGC 224", "M 31")
  end

  it "returns nil when nothing matches and raises on HTTP errors" do
    stubs.get("/v2.0/targets/search") { [ 200, {}, { page_results: [] }.to_json ] }
    expect(client.search("zzz")).to be_nil

    stubs2 = Faraday::Adapter::Test::Stubs.new { |s| s.get("/targets/search") { [ 503, {}, "" ] } }
    failing = described_class.new(api_key: "k", connection: Faraday.new(url: "https://x/") { |f| f.adapter :test, stubs2 })
    expect { failing.search("m1") }.to raise_error(/503/)
  end
end
