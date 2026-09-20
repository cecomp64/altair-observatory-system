FactoryBot.define do
  factory :target do
    user
    telescope
    sequence(:name) { |n| "Test Target #{n}" }
    ra_deg { 83.822 }
    dec_deg { -5.391 }
    status { :submitted }
    priority { 0 }
    notes { "Some notes" }
    submitted_at { Time.current }
  end
end
