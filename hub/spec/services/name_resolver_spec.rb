require "rails_helper"

RSpec.describe Catalogue::NameResolver do
  let(:client) { instance_double(Catalogue::TelescopiusClient) }
  let(:resolver) { described_class.new(client: client) }

  # The test environment uses a null cache store; misses are cached in production.
  before { allow(Rails).to receive(:cache).and_return(ActiveSupport::Cache::MemoryStore.new) }

  it "answers from the local catalogue without calling Telescopius" do
    object = create(:astro_object, primary_name: "Crab Nebula").tap { |o| o.add_alias("M 1") }
    expect(client).not_to receive(:search)
    expect(resolver.resolve("m1")).to eq([ object, :local ])
  end

  it "stores a Telescopius answer with its aliases, so the next lookup is local" do
    allow(client).to receive(:search).once.and_return(
      Catalogue::TelescopiusClient::Result.new(name: "Pacman Nebula", ra_deg: 13.2, dec_deg: 56.6, object_type: "Emission Nebula", aliases: [ "NGC 281", "Sh2-184" ])
    )
    object, outcome = resolver.resolve("pacman")

    expect(outcome).to eq(:telescopius)
    expect(object.alias_names).to include("Pacman Nebula", "pacman", "NGC 281", "Sh2-184")
    expect(resolver.resolve("sh2184")).to eq([ object, :local ])
  end

  it "merges a Telescopius answer into an object we already know under another name" do
    existing = create(:astro_object, primary_name: "NGC 281")
    allow(client).to receive(:search).and_return(
      Catalogue::TelescopiusClient::Result.new(name: "Pacman Nebula", ra_deg: 13.2, dec_deg: 56.6, aliases: [ "NGC 281" ])
    )
    expect(resolver.resolve("Pacman Nebula")).to eq([ existing, :telescopius ])
  end

  it "caches misses and reports errors without raising" do
    allow(client).to receive(:search).with("nothing").once.and_return(nil)
    expect(resolver.resolve("nothing")).to eq([ nil, :not_found ])
    expect(resolver.resolve("nothing")).to eq([ nil, :not_found ])

    allow(client).to receive(:search).with("boom").and_raise("HTTP 500")
    expect(resolver.resolve("boom")).to eq([ nil, :error ])
  end
end
