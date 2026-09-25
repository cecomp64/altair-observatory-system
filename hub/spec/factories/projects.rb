FactoryBot.define do
  factory :project do
    user
    sequence(:name) { |n| "Test Project #{n}" }
    status { :active }
    priority { 0 }
  end
end
