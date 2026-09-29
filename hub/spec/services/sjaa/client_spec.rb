require "rails_helper"

RSpec.describe Sjaa::Client do
  let(:stubs) { Faraday::Adapter::Test::Stubs.new }
  let(:connection) { Faraday.new(url: "https://membership.sjaa.net/api") { |f| f.adapter :test, stubs } }
  let(:client) { described_class.new(api_token: "t", connection: connection) }

  def person(id:, email:, memberships: [], **attrs)
    { id: id, email: email, first_name: "Ada", last_name: "Lovelace", memberships: memberships }.merge(attrs)
  end

  it "finds the person whose email matches exactly, among SJAA's substring matches" do
    stubs.get("/api/people") do |env|
      expect(env.params).to include("email" => "ada@example.com")
      [ 200, { "Content-Type" => "application/json" }, {
        people: [
          person(id: 1, email: "grace.ada@example.com"),
          person(id: 2, email: "Ada@Example.com", memberships: [ { start: "2026-01-01T00:00:00Z", term_months: 12, end: "2026-12-31T23:59:59Z" } ])
        ],
        next_page: nil
      }.to_json ]
    end

    found = client.find_person_by_email(" ADA@example.com ")
    expect(found).to have_attributes(id: 2, name: "Ada Lovelace", email: "ada@example.com")
    expect(found.membership_expires_on).to eq(Date.new(2026, 12, 31))
    expect(found.active_member?(Date.new(2026, 12, 31))).to be true
    expect(found.active_member?(Date.new(2027, 1, 1))).to be false
  end

  it "matches secondary contact emails, follows next_page and accepts a bare array" do
    stubs.get("/api/people") do |env|
      if env.params["page"] == "2"
        [ 200, {}, [ person(id: 7, email: "main@example.com", contacts: [ { email: "ada@example.com" } ]) ].to_json ]
      else
        [ 200, {}, { people: [], next_page: 2 }.to_json ]
      end
    end

    expect(client.find_person_by_email("ada@example.com")&.id).to eq(7)
  end

  it "returns nil for no match or an ambiguous one" do
    stubs.get("/api/people") do
      [ 200, {}, { people: [ person(id: 1, email: "a@example.com"), person(id: 2, email: "a@example.com") ] }.to_json ]
    end

    expect(client.find_person_by_email("a@example.com")).to be_nil
    expect(client.find_person_by_email("b@example.com")).to be_nil
  end

  it "raises Sjaa::Error on HTTP errors" do
    stubs.get("/api/people") { [ 401, {}, { errors: [ "Unauthorized" ] }.to_json ] }

    expect { client.find_person_by_email("a@example.com") }.to raise_error(Sjaa::Error, /401/)
  end
end

RSpec.describe Sjaa::Person do
  it "treats a membership with no end and no term as lifetime" do
    person = described_class.from_api("id" => 1, "email" => "a@example.com", "memberships" => [ { "start" => "2001-05-01", "term_months" => nil, "end" => nil } ])
    expect(person.active_member?).to be true
    expect(person.membership_expires_on).to be_nil
  end

  it "derives the end from start and term when end is missing, and is inactive with no memberships" do
    person = described_class.from_api("id" => 1, "memberships" => [ { "start" => "2025-03-15", "term_months" => 12 } ])
    expect(person.membership_expires_on).to eq(Date.new(2026, 3, 31))
    expect(described_class.from_api("id" => 2, "memberships" => []).active_member?).to be false
  end
end
