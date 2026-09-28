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
    it "is empty when no horizon file is attached" do
      expect(build(:telescope).horizon_points).to eq([])
    end

    it "caches the az,alt pairs of an attached file" do
      telescope = create(:telescope)
      telescope.horizon_file.attach(
        io: StringIO.new("# comment\n10,20\n0,15\n"),
        filename: "horizon.csv",
        content_type: "text/csv"
      )

      expect(telescope.reload.horizon_points).to eq([ [ 0.0, 15.0 ], [ 10.0, 20.0 ] ])
      expect(telescope.horizon_file.download).to eq("# comment\n10,20\n0,15\n")
    end

    it "caches an uploaded file and replaces the points when a new one is uploaded" do
      telescope = create(:telescope)
      upload = ->(text) { Rack::Test::UploadedFile.new(StringIO.new(text), "text/csv", original_filename: "horizon.csv") }

      telescope.update!(horizon_file: upload.("0 10\n180 20\n"))
      expect(telescope.reload.horizon_points).to eq([ [ 0.0, 10.0 ], [ 180.0, 20.0 ] ])

      telescope.update!(horizon_file: upload.("90,30\n"))
      expect(telescope.reload.horizon_points).to eq([ [ 90.0, 30.0 ] ])
    end

    it "rejects a file that doesn't parse and keeps the cached points" do
      telescope = create(:telescope)
      telescope.horizon_file.attach(io: StringIO.new("0,15\n"), filename: "horizon.csv")

      telescope.horizon_file = Rack::Test::UploadedFile.new(StringIO.new("0,15\n400,20\n"), "text/csv", original_filename: "horizon.csv")
      expect(telescope.save).to be(false)
      expect(telescope.errors.full_messages).to eq([ "Horizon file line 2: azimuth must be between 0 and 360" ])
      expect(telescope.reload.horizon_points).to eq([ [ 0.0, 15.0 ] ])
      expect(telescope.horizon_file.download).to eq("0,15\n")
    end

    it "rejects a file over 1 MB without reading it" do
      telescope = build(:telescope)
      telescope.horizon_file.attach(io: StringIO.new("0,15\n" * 300_000), filename: "horizon.csv")

      expect(telescope).not_to be_valid
      expect(telescope.errors[:horizon_file]).to eq([ "is larger than 1 MB" ])
    end

    it "is emptied when the file is detached" do
      telescope = create(:telescope)
      telescope.horizon_file.attach(io: StringIO.new("0,15\n"), filename: "horizon.csv")

      telescope.update!(horizon_file: nil)
      expect(telescope.reload.horizon_points).to eq([])
    end
  end
end
