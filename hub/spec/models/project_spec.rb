require "rails_helper"

RSpec.describe Project, type: :model do
  it { is_expected.to belong_to(:user) }
  it { is_expected.to have_many(:targets).dependent(:destroy) }
  it { is_expected.to validate_presence_of(:name) }

  it "names its Target Scheduler project #P<id> <name>" do
    project = create(:project, name: "Andromeda deep")
    expect(project.ts_project_name).to eq("#P#{project.id} Andromeda deep")
  end

  it "merges processing settings over Altair's defaults, nested and without nils" do
    project = build(:project, processing_settings: { "multi_night" => { "mode" => "frame_reintegration" }, "drizzle_scale" => nil })
    settings = project.effective_processing_settings
    expect(settings["multi_night"]).to eq("enabled" => true, "mode" => "frame_reintegration")
    expect(settings["drizzle_scale"]).to eq(1)
  end
end
