require "rails_helper"

RSpec.describe Telescope, type: :model do
  subject { create(:telescope) }

  it { is_expected.to validate_presence_of(:name) }
  it { is_expected.to validate_uniqueness_of(:slug) }
  it { is_expected.to have_many(:targets).dependent(:destroy) }
  it { is_expected.to have_many(:api_keys).dependent(:destroy) }

  it "auto-generates a slug from the name when not given" do
    telescope = create(:telescope, name: "Club 16 Inch", slug: nil)
    expect(telescope.slug).to eq("club-16-inch")
  end

  describe "#horizon_points" do
    it "returns an empty array when no horizon file is attached" do
      expect(build(:telescope).horizon_points).to eq([])
    end

    it "parses az,alt pairs from the attached file" do
      telescope = create(:telescope)
      telescope.horizon_file.attach(
        io: StringIO.new("# comment\n10,20\n0,15\n"),
        filename: "horizon.csv",
        content_type: "text/csv"
      )

      expect(telescope.horizon_points).to eq([ [ 0.0, 15.0 ], [ 10.0, 20.0 ] ])
    end
  end
end
