require "rails_helper"

RSpec.describe AstroObject, type: :model do
  it "adds its primary name as an alias" do
    object = create(:astro_object, primary_name: "Andromeda Galaxy")
    expect(object.aliases.map(&:name)).to eq([ "Andromeda Galaxy" ])
  end

  it "finds objects by any normalised alias" do
    object = create(:astro_object, primary_name: "Andromeda Galaxy")
    object.add_alias("NGC 224")
    object.add_alias("M 31")

    expect(AstroObject.find_by_alias("m31")).to eq(object)
    expect(AstroObject.find_by_alias("NGC0224")).to eq(object)
  end

  it "doesn't add the same alias twice" do
    object = create(:astro_object, primary_name: "M 31")
    object.add_alias("M31")
    expect(object.aliases.count).to eq(1)
  end

  it "ranks exact alias hits first in search" do
    m1 = create(:astro_object, primary_name: "Crab Nebula").tap { |o| o.add_alias("M 1") }
    create(:astro_object, primary_name: "M 101")
    create(:astro_object, primary_name: "M 13")

    expect(AstroObject.search("m1").first).to eq(m1)
    expect(AstroObject.search("crab").to_a).to eq([ m1 ])
  end
end
