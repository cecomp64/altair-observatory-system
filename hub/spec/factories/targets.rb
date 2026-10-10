FactoryBot.define do
  factory :target do
    user
    telescope
    project { association :project, user: user }
    sequence(:name) { |n| "Test Target #{n}" }
    ra_deg { 83.822 }
    dec_deg { -5.391 }
    status { :submitted }
    priority { :normal }
    notes { "Some notes" }
    submitted_at { Time.current }
  end
end
