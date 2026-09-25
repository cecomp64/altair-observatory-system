require "rails_helper"

RSpec.describe Progress::Calculator do
  let(:project) { create(:project) }

  before do
    a = create(:target, project: project, user: project.user)
    b = create(:target, project: project, user: project.user)
    create(:exposure_plan, target: a, filter: "Ha", exposure_seconds: 300, desired_count: 10, completed_count: 10, collected_count: 8, integrated_count: 4)
    create(:exposure_plan, target: b, filter: "Ha", exposure_seconds: 300, desired_count: 10, completed_count: 2)
    create(:exposure_plan, target: a, filter: "OIII", exposure_seconds: 600, desired_count: 5, completed_count: 9)
  end

  it "sums goal and actual seconds per filter, capping each plan at its goal" do
    ha, oiii = described_class.new(project).by_filter
    expect(ha.goal_seconds).to eq(6000)
    expect(ha.actual_seconds).to eq("acquired" => 3600, "collected" => 2400, "integrated" => 1200)
    expect(ha.percent("acquired")).to eq(60.0)
    expect(oiii.percent("acquired")).to eq(100.0)
  end

  it "averages filters for the overall percentage and recommends the filter furthest behind" do
    calculator = described_class.new(project)
    expect(calculator.overall_percent).to eq(80.0)
    expect(calculator.recommended_filter).to eq("Ha")
  end
end
