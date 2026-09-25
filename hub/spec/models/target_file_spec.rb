require "rails_helper"

RSpec.describe TargetFile, type: :model do
  it { is_expected.to belong_to(:target) }
  it { is_expected.to validate_presence_of(:url) }
  it { is_expected.to define_enum_for(:kind).with_values(sub: 0, stacked: 1, preview: 2, log: 3) }
end
