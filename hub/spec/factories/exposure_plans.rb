FactoryBot.define do
  factory :exposure_plan do
    target
    filter { "Luminance" }
    exposure_seconds { 300 }
    desired_count { 20 }
    completed_count { 0 }
  end
end
