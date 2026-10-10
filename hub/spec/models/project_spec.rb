require "rails_helper"

RSpec.describe Project, type: :model do
  it { is_expected.to belong_to(:user) }
  it { is_expected.to have_many(:targets).dependent(:destroy) }
  it { is_expected.to validate_presence_of(:name) }

  it "names its Target Scheduler project #P<id> <name>" do
    project = create(:project, name: "Andromeda deep")
    expect(project.ts_project_name).to eq("#P#{project.id} Andromeda deep")
  end

  it "has a low, normal or high priority, normal by default, with Target Scheduler's code" do
    expect(described_class.new).to have_attributes(priority: "normal", priority_code: 1)
    expect(build(:project, priority: "high")).to have_attributes(priority_code: 2).and be_valid
    expect(build(:project, priority: "urgent")).not_to be_valid
  end

  it "maps free-form numbers to a level" do
    expect([ -2, 0, nil, 1, 7 ].map { |n| Prioritized.level_for(n) }).to eq(%w[low normal normal high high])
  end

  it "merges processing settings over Altair's defaults, nested and without nils" do
    project = build(:project, processing_settings: { "multi_night" => { "mode" => "frame_reintegration" }, "drizzle_scale" => nil })
    settings = project.effective_processing_settings
    expect(settings["multi_night"]).to eq("enabled" => true, "mode" => "frame_reintegration")
    expect(settings["drizzle_scale"]).to eq(1)
  end
end
