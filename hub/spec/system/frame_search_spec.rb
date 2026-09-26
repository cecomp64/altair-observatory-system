require "rails_helper"

RSpec.describe "Frame search", type: :system do
  let(:admin) { create(:user, :admin) }
  let(:telescope) { create(:telescope) }
  let!(:train) { create(:optical_train, telescope: telescope) }
  let(:node) { create(:processing_node).tap { |n| n.telescopes << telescope } }
  let(:target) { create(:target, telescope: telescope, optical_train: train, name: "M31") }

  def frame(n, **attrs)
    Frame.create!({ sha256: Digest::SHA256.hexdigest("f#{n}"), processing_node: node, altair_frame_id: n, telescope: telescope,
                    optical_train: train, image_type: "light", night: Date.new(2026, 9, 24), date_obs: Time.utc(2026, 9, 25, 6, n),
                    file_name: "frame_#{n}.fits", logical_path: "raw/x/#{n}.fits", status: "valid", origin: "collect",
                    assignment_source: "unlinked", filter: "Ha", exposure_s: 300 }.merge(attrs))
  end

  before do
    target
    frame(1)
    frame(2)
    frame(3, filter: "OIII")
    sign_in admin
  end

  it "filters by filter and bulk-assigns the selected frames to a target" do
    visit frames_path
    expect(page).to have_text("frame_1.fits").and have_text("frame_3.fits")

    select "Ha", from: "filter"
    click_on "Search"
    expect(page).to have_no_text("frame_3.fits")
    expect(page).to have_current_path(/filter=Ha/)

    find("input[data-bulk-select-target='all']").click
    expect(page).to have_text("2 selected")
    select "M31 (#{telescope.name})", from: "target_id"
    click_on "Assign selected to target"

    expect(page).to have_text("M31")
    expect(Frame.where(target: target).pluck(:file_name)).to contain_exactly("frame_1.fits", "frame_2.fits")
    expect(Frame.find_by(file_name: "frame_1.fits").assignment_source).to eq("manual")
  end
end
